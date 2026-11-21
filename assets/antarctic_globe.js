(() => {
  // Render uploaded observations as one smooth colour surface on a MapLibre
  // globe. Points are placed back on the regular grid they were sampled on
  // and interpolated bilinearly into raster tiles, so the field is continuous
  // at every zoom, covers the pole, and fades out softly where data ends.
  const activeMaps = new Map();
  const entriesById = new Map();
  let nextEntryId = 1;

  const sourceId = "antarctic-observation-surface";
  const layerId = "antarctic-observation-surface";
  const protocol = "antarcticsurface";
  const tileSize = 256;
  const surfaceOpacity = 0.86;

  // Concentrations below the conventional 15 % ice-edge threshold fade into
  // the ocean so the ice edge does not end in a hard rim.
  const iceEdgePercent = 15;
  // Width, in degrees of latitude, of the soft fade at the grid's outer edge.
  const edgeFadeDegrees = 4;
  const kmPerDegree = 111.32;

  // NSIDC polar stereographic south (Hughes 1980 ellipsoid, true scale at
  // 70°S). The bundled sea-ice / SST grids are a regular 25 km lattice in
  // this plane, so interpolating there reproduces the grid exactly.
  const ellipsoidA = 6378.273;
  const ellipsoidE = 0.081816153;
  const stereoT = (phi) => {
    const s = ellipsoidE * Math.sin(phi);
    return (
      Math.tan(Math.PI / 4 - phi / 2) /
      ((1 - s) / (1 + s)) ** (ellipsoidE / 2)
    );
  };
  const standardParallel = (70 * Math.PI) / 180;
  const stereoScale =
    (ellipsoidA * Math.cos(standardParallel)) /
    Math.sqrt(1 - (ellipsoidE * Math.sin(standardParallel)) ** 2) /
    stereoT(standardParallel);
  // Distance from the South Pole in the stereographic plane, in km.
  const stereoRadius = (latitude) =>
    stereoScale * stereoT((-latitude * Math.PI) / 180);

  // Map measurement and signed-difference values to the two legend scales.
  const colorAt = (value) => {
    const stops = [
      [0, [23, 50, 77]],
      [30, [40, 120, 165]],
      [58, [102, 184, 165]],
      [78, [242, 223, 119]],
      [100, [217, 75, 55]],
    ];
    const upper = stops.findIndex(([limit]) => value <= limit);
    if (upper < 0) return stops[stops.length - 1][1];
    if (upper === 0) return stops[0][1];
    const [lowValue, lowColor] = stops[upper - 1];
    const [highValue, highColor] = stops[upper];
    const amount = (value - lowValue) / (highValue - lowValue);
    return lowColor.map((channel, index) =>
      Math.round(channel + (highColor[index] - channel) * amount),
    );
  };

  const differenceColorAt = (value, maxAbs) => {
    const neutral = [242, 244, 246];
    const negative = [38, 105, 154];
    const positive = [190, 56, 48];
    const amount =
      maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) : 0;
    const endpoint = value < 0 ? negative : positive;
    return [
      ...neutral.map((channel, index) =>
        Math.round(channel + (endpoint[index] - channel) * amount),
      ),
      // Near-zero change stays faint so it does not hide the basemap.
      Math.max(0.1, Math.min(1, amount * 2.5)),
    ];
  };

  const smoothstep = (low, high, value) => {
    const t = Math.max(0, Math.min(1, (value - low) / (high - low)));
    return t * t * (3 - 2 * t);
  };

  // The source data is often a rectangular polar-stereographic grid. On a
  // round globe its corners read as a square frame, so find the circle that
  // fits inside the grid (the southernmost of the grid's northern edges) and
  // fade the surface out towards it.
  const gridEdgeLatitude = (points) => {
    const northernmost = new Map();
    for (const point of points) {
      const bin = Math.floor((Number(point[0]) + 180) / 5);
      const latitude = Number(point[1]);
      if (!(northernmost.get(bin) >= latitude)) {
        northernmost.set(bin, latitude);
      }
    }
    // Only clip data that wraps all the way around the pole.
    if (northernmost.size < 72) return null;
    return Math.min(...northernmost.values());
  };

  const isConcentrationPoint = (entry, point) =>
    entry.colorMode === "difference"
      ? point[7] === "%"
      : entry.isConcentration;

  // Points that can be clicked: open water (0 % concentration, or 0 % in both
  // compared snapshots) is drawn as transparent, so it is not inspectable.
  const clickablePoints = (entry) =>
    entry.points.filter((point) => {
      if (isConcentrationPoint(entry, point)) {
        return entry.colorMode === "difference"
          ? Number(point[3]) > 0 || Number(point[4]) > 0
          : Number(point[3]) > 0;
      }
      return (
        entry.edgeLatitude === null || Number(point[1]) < entry.edgeLatitude
      );
    });

  // ---------------------------------------------------------------------
  // Field reconstruction. Observations are placed on the regular lattice
  // they were sampled on (latitude/longitude or polar stereographic), then
  // sampled with bilinear interpolation. This keeps every grid faithful,
  // gives smooth gradients at any zoom, and avoids stripes from uneven
  // point spacing. Scattered data falls back to a Gaussian-blended lattice.
  // ---------------------------------------------------------------------

  const lonLatPlane = {
    name: "lonlat",
    forward: (longitude, latitude) => [longitude, latitude],
  };
  const stereoPlane = {
    name: "stereo",
    forward: (longitude, latitude) => {
      const radius = stereoRadius(latitude);
      const lambda = (longitude * Math.PI) / 180;
      return [radius * Math.sin(lambda), radius * Math.cos(lambda)];
    },
  };

  // Find a regular spacing for one coordinate axis, or null.
  const detectAxis = (coordinates, precision) => {
    const unique = Array.from(
      new Set(coordinates.map((value) => Math.round(value / precision))),
    ).sort((a, b) => a - b);
    if (unique.length < 2) return null;
    const differences = [];
    for (let index = 1; index < unique.length; index += 1) {
      differences.push(unique[index] - unique[index - 1]);
    }
    differences.sort((a, b) => a - b);
    const step = differences[Math.floor(differences.length / 2)] * precision;
    if (!(step > 0)) return null;
    const origin = unique[0] * precision;
    let aligned = 0;
    for (const value of coordinates) {
      const offset = (value - origin) / step;
      if (Math.abs(offset - Math.round(offset)) < 0.1) aligned += 1;
    }
    if (aligned < coordinates.length * 0.95) return null;
    return {
      origin,
      step,
      count: Math.round((unique[unique.length - 1] * precision - origin) / step) + 1,
    };
  };

  const createLattice = (plane, xAxis, yAxis, wrapX) => {
    const nx = wrapX ? Math.round(360 / xAxis.step) : xAxis.count;
    const ny = yAxis.count;
    const size = nx * ny;
    return {
      plane,
      x0: xAxis.origin,
      dx: xAxis.step,
      nx,
      y0: yAxis.origin,
      dy: yAxis.step,
      ny,
      wrapX,
      sum: new Float64Array(size),
      auxSum: new Float64Array(size),
      count: new Float32Array(size),
    };
  };

  const finishLattice = (lattice) => {
    const size = lattice.nx * lattice.ny;
    lattice.value = new Float32Array(size).fill(NaN);
    lattice.aux = new Float32Array(size).fill(NaN);
    for (let index = 0; index < size; index += 1) {
      const count = lattice.count[index];
      if (count > 0) {
        lattice.value[index] = lattice.sum[index] / count;
        lattice.aux[index] = lattice.auxSum[index] / count;
      }
    }
    delete lattice.sum;
    delete lattice.auxSum;
    delete lattice.count;
    return lattice;
  };

  const latticeIndex = (lattice, x, y) => {
    let i = Math.round((x - lattice.x0) / lattice.dx);
    const j = Math.round((y - lattice.y0) / lattice.dy);
    if (lattice.wrapX) i = ((i % lattice.nx) + lattice.nx) % lattice.nx;
    if (i < 0 || i >= lattice.nx || j < 0 || j >= lattice.ny) return -1;
    return j * lattice.nx + i;
  };

  // Try to recognise the points as a regular lattice in one of the planes.
  const regularLattice = (samples) => {
    const candidates = [
      { plane: lonLatPlane, precision: 1e-5 },
      { plane: stereoPlane, precision: 0.01 },
    ];
    for (const { plane, precision } of candidates) {
      const xs = [];
      const ys = [];
      for (const sample of samples) {
        const [x, y] = plane.forward(sample.longitude, sample.latitude);
        sample[plane.name] = [x, y];
        xs.push(x);
        ys.push(y);
      }
      const xAxis = detectAxis(xs, precision);
      const yAxis = detectAxis(ys, precision);
      if (!xAxis || !yAxis) continue;
      const wrapX =
        plane === lonLatPlane && xAxis.count * xAxis.step >= 360 - xAxis.step * 1.5;
      const nx = wrapX ? Math.round(360 / xAxis.step) : xAxis.count;
      const cells = nx * yAxis.count;
      // Reject spacings so fine that the lattice is mostly empty (that means
      // the data is not really regular in this plane).
      if (cells > 6e6 || samples.length < cells * 0.1) continue;
      const lattice = createLattice(plane, xAxis, yAxis, wrapX);
      for (const sample of samples) {
        const [x, y] = sample[plane.name];
        const index = latticeIndex(lattice, x, y);
        if (index < 0) continue;
        lattice.sum[index] += sample.value;
        lattice.auxSum[index] += sample.aux;
        lattice.count[index] += 1;
      }
      return finishLattice(lattice);
    }
    return null;
  };

  // Irregular data: blend into a stereographic lattice whose spacing matches
  // the typical distance between observations; cells far from any
  // observation stay empty.
  const blendedLattice = (samples) => {
    const coordinates = samples.map((sample) =>
      stereoPlane.forward(sample.longitude, sample.latitude),
    );
    let maxRadius = 1;
    for (const [x, y] of coordinates) {
      maxRadius = Math.max(maxRadius, Math.hypot(x, y));
    }
    // Estimate spacing from the area the points actually occupy.
    let spacing = Math.sqrt((Math.PI * maxRadius * maxRadius) / samples.length);
    for (let iteration = 0; iteration < 3; iteration += 1) {
      const occupied = new Set();
      for (const [x, y] of coordinates) {
        occupied.add(`${Math.floor(x / spacing)},${Math.floor(y / spacing)}`);
      }
      spacing = Math.sqrt((occupied.size * spacing * spacing) / samples.length);
    }
    spacing = Math.max(maxRadius / 700, Math.min(200, spacing));
    const axis = {
      origin: -maxRadius - 2 * spacing,
      step: spacing,
      count: Math.ceil((2 * maxRadius + 4 * spacing) / spacing) + 1,
    };
    const lattice = createLattice(stereoPlane, axis, axis, false);
    const sigma = 0.75;
    const inverse = 1 / (2 * sigma * sigma);
    for (let index = 0; index < samples.length; index += 1) {
      const [x, y] = coordinates[index];
      const fx = (x - lattice.x0) / spacing;
      const fy = (y - lattice.y0) / spacing;
      for (let j = Math.floor(fy - 2); j <= Math.ceil(fy + 2); j += 1) {
        for (let i = Math.floor(fx - 2); i <= Math.ceil(fx + 2); i += 1) {
          if (i < 0 || j < 0 || i >= lattice.nx || j >= lattice.ny) continue;
          const w = Math.exp(-((i - fx) ** 2 + (j - fy) ** 2) * inverse);
          const cell = j * lattice.nx + i;
          lattice.sum[cell] += w * samples[index].value;
          lattice.auxSum[cell] += w * samples[index].aux;
          lattice.count[cell] += w;
        }
      }
    }
    // Require meaningful support so isolated far-away cells stay empty.
    for (let cell = 0; cell < lattice.count.length; cell += 1) {
      if (lattice.count[cell] < 0.35) lattice.count[cell] = 0;
    }
    return finishLattice(lattice);
  };

  const buildField = (entry) => {
    entry.isConcentration =
      entry.colorMode === "measurement" &&
      /concentration/i.test(
        entry.element.getAttribute("data-globe-measurement") || "",
      );
    entry.edgeLatitude = gridEdgeLatitude(entry.points);
    entry.iceFade = entry.points.some((point) =>
      isConcentrationPoint(entry, point),
    );
    const samples = [];
    let northernmost = -90;
    for (const point of entry.points) {
      const longitude = Number(point[0]);
      const latitude = Number(point[1]);
      const value = Number(point[2]);
      if (![longitude, latitude, value].every(Number.isFinite)) continue;
      // Auxiliary channel: concentration that controls the ice-edge fade
      // (for a difference, the larger of the two compared means).
      let aux = 100;
      if (isConcentrationPoint(entry, point)) {
        aux =
          entry.colorMode === "difference"
            ? Math.max(Number(point[3]) || 0, Number(point[4]) || 0)
            : Number(point[3]) || 0;
      }
      samples.push({ longitude, latitude, value, aux });
      northernmost = Math.max(northernmost, latitude);
    }
    if (!samples.length) return null;
    const lattice = regularLattice(samples) || blendedLattice(samples);
    lattice.northernLimit = Math.min(
      85,
      (entry.edgeLatitude ?? northernmost) + 3,
    );
    lattice.polar = polarMean(lattice);
    return lattice;
  };

  // MapLibre's globe fills the cap south of 85.05°S by stretching each
  // tile's edge row to the pole, which turns longitude detail into a fan.
  // Blend towards the mean around the cap edge so the cap is one colour.
  const polarCapStart = -80;
  const polarCapEdge = -85.05;
  const projectedSample = (lattice, longitude, latitude, out) => {
    if (lattice.plane === stereoPlane) {
      const radius = stereoRadius(latitude);
      const lambda = (longitude * Math.PI) / 180;
      return sampleField(
        lattice,
        radius * Math.sin(lambda),
        radius * Math.cos(lambda),
        out,
      );
    }
    return sampleField(lattice, longitude, latitude, out);
  };
  const polarMean = (lattice) => {
    const sample = { coverage: 0, value: 0, aux: 0 };
    let coverage = 0;
    let value = 0;
    let aux = 0;
    const steps = 720;
    for (let step = 0; step < steps; step += 1) {
      projectedSample(lattice, -180 + (step + 0.5) * (360 / steps), polarCapEdge, sample);
      coverage += sample.coverage;
      if (sample.coverage > 0) {
        value += sample.coverage * sample.value;
        aux += sample.coverage * sample.aux;
      }
    }
    if (coverage <= 0) return null;
    return {
      coverage: coverage / steps,
      value: value / coverage,
      aux: aux / coverage,
    };
  };
  const blendPolar = (lattice, latitude, sample) => {
    const polar = lattice.polar;
    if (!polar || latitude > polarCapStart) return;
    const t = smoothstep(
      0,
      1,
      (polarCapStart - latitude) / (polarCapStart - polarCapEdge),
    );
    if (sample.coverage <= 0) {
      sample.value = polar.value;
      sample.aux = polar.aux;
    } else {
      sample.value += (polar.value - sample.value) * t;
      sample.aux += (polar.aux - sample.aux) * t;
    }
    sample.coverage += (polar.coverage - sample.coverage) * t;
  };

  // Bilinear sample of the lattice. Returns coverage (0–1, the share of the
  // four neighbouring cells that hold data) and the interpolated channels.
  const sampleField = (lattice, x, y, out) => {
    const fx = (x - lattice.x0) / lattice.dx;
    const fy = (y - lattice.y0) / lattice.dy;
    const i0 = Math.floor(fx);
    const j0 = Math.floor(fy);
    const tx = fx - i0;
    const ty = fy - j0;
    let weight = 0;
    let value = 0;
    let aux = 0;
    for (let corner = 0; corner < 4; corner += 1) {
      let i = i0 + (corner & 1);
      const j = j0 + (corner >> 1);
      if (lattice.wrapX) i = ((i % lattice.nx) + lattice.nx) % lattice.nx;
      if (i < 0 || j < 0 || i >= lattice.nx || j >= lattice.ny) continue;
      const cell = j * lattice.nx + i;
      const cellValue = lattice.value[cell];
      if (Number.isNaN(cellValue)) continue;
      const w = (corner & 1 ? tx : 1 - tx) * (corner >> 1 ? ty : 1 - ty);
      weight += w;
      value += w * cellValue;
      aux += w * lattice.aux[cell];
    }
    out.coverage = weight;
    if (weight > 0) {
      out.value = value / weight;
      out.aux = aux / weight;
    }
    return out;
  };

  // ---------------------------------------------------------------------
  // Tile rendering. The field is served to MapLibre as a raster tile
  // source through a custom protocol. Tiles get proper level-of-detail
  // (no speckle when zoomed out) and MapLibre's globe extends the edge
  // tiles over the pole, so there is no hole south of 85°S.
  // ---------------------------------------------------------------------

  let blankTile = null;
  const encodeTile = async (canvas) => {
    if (canvas.convertToBlob) {
      return (await canvas.convertToBlob({ type: "image/png" })).arrayBuffer();
    }
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob) blob.arrayBuffer().then(resolve, reject);
        else reject(new Error("Tile encoding failed."));
      }, "image/png");
    });
  };
  const makeCanvas = () => {
    if (typeof OffscreenCanvas !== "undefined") {
      return new OffscreenCanvas(tileSize, tileSize);
    }
    const canvas = document.createElement("canvas");
    canvas.width = tileSize;
    canvas.height = tileSize;
    return canvas;
  };
  const emptyTile = async () => {
    if (!blankTile) blankTile = await encodeTile(makeCanvas());
    return blankTile.slice(0);
  };

  const tileLatitude = (z, y, pixelRow) => {
    const n = Math.PI * (1 - (2 * (y * tileSize + pixelRow)) / (tileSize * 2 ** z));
    return (Math.atan(Math.sinh(n)) * 180) / Math.PI;
  };

  const renderTile = async (entry, z, x, y) => {
    const lattice = entry.field;
    if (!lattice) return emptyTile();
    // Whole tile north of the data: nothing to draw.
    if (tileLatitude(z, y, tileSize) > lattice.northernLimit) {
      return emptyTile();
    }
    const canvas = makeCanvas();
    const context = canvas.getContext("2d");
    const image = context.createImageData(tileSize, tileSize);
    const pixels = image.data;
    const worldPixels = tileSize * 2 ** z;
    const longitudes = new Float64Array(tileSize);
    const sines = new Float64Array(tileSize);
    const cosines = new Float64Array(tileSize);
    for (let column = 0; column < tileSize; column += 1) {
      const longitude =
        ((x * tileSize + column + 0.5) / worldPixels) * 360 - 180;
      longitudes[column] = longitude;
      sines[column] = Math.sin((longitude * Math.PI) / 180);
      cosines[column] = Math.cos((longitude * Math.PI) / 180);
    }
    const stereo = lattice.plane === stereoPlane;
    const sample = { coverage: 0, value: 0, aux: 0 };
    const fadeEdge =
      entry.edgeLatitude !== null && !entry.isConcentration;
    for (let row = 0; row < tileSize; row += 1) {
      const latitude = tileLatitude(z, y, row + 0.5);
      if (latitude > lattice.northernLimit) continue;
      let rowFade = 1;
      if (fadeEdge) {
        rowFade = smoothstep(
          0,
          1,
          (entry.edgeLatitude - latitude) / edgeFadeDegrees,
        );
        if (rowFade <= 0) continue;
      }
      const radius = stereo ? stereoRadius(latitude) : 0;
      for (let column = 0; column < tileSize; column += 1) {
        if (stereo) {
          sampleField(
            lattice,
            radius * sines[column],
            radius * cosines[column],
            sample,
          );
        } else {
          sampleField(lattice, longitudes[column], latitude, sample);
        }
        blendPolar(lattice, latitude, sample);
        if (sample.coverage <= 0) continue;
        let color;
        let alpha;
        if (entry.colorMode === "difference") {
          color = differenceColorAt(sample.value, entry.maxAbs);
          alpha = color[3];
        } else {
          color = colorAt(sample.value);
          alpha = 0.92;
        }
        if (entry.iceFade) {
          // Sea ice: fade out across the ice edge into open water.
          alpha *= smoothstep(iceEdgePercent - 10, iceEdgePercent + 20, sample.aux);
        }
        // Soft coastline / data edge: half-way between a filled and an
        // empty cell the surface is half transparent.
        alpha *= smoothstep(0.2, 0.8, sample.coverage) * rowFade;
        if (alpha <= 0.004) continue;
        const offset = (row * tileSize + column) * 4;
        pixels[offset] = color[0];
        pixels[offset + 1] = color[1];
        pixels[offset + 2] = color[2];
        pixels[offset + 3] = Math.round(255 * alpha);
      }
    }
    context.putImageData(image, 0, 0);
    return encodeTile(canvas);
  };

  let protocolRegistered = false;
  const registerProtocol = () => {
    if (protocolRegistered) return;
    protocolRegistered = true;
    window.maplibregl.addProtocol(protocol, async (params) => {
      const match = params.url.match(
        /^antarcticsurface:\/\/(\d+)\/(\d+)\/(\d+)\/(\d+)\/(\d+)/,
      );
      if (!match) throw new Error(`Unexpected surface tile ${params.url}`);
      const [, id, version, z, x, y] = match.map(Number);
      const entry = entriesById.get(id);
      if (!entry || entry.version !== version) {
        return { data: await emptyTile() };
      }
      return { data: await renderTile(entry, z, x, y) };
    });
  };

  // OpenStreetMap draws Antarctic ice shelves as water, and the Ross Ice
  // Shelf reaches the edge of the Web Mercator world (85.05°S). The globe
  // stretches that edge row over the pole, so the cap showed a blue wedge.
  // Everything at that latitude is land ice or ice shelf, so paint a thin
  // ice-coloured strip along the edge; the cap then reads as ice.
  const polarIceId = "antarctic-polar-ice";
  const addPolarIce = (map) => {
    const ring = [];
    for (let longitude = -180; longitude <= 180; longitude += 5) {
      ring.push([longitude, -84.9]);
    }
    for (let longitude = 180; longitude >= -180; longitude -= 5) {
      ring.push([longitude, -85.06]);
    }
    ring.push(ring[0]);
    map.addSource(polarIceId, {
      type: "geojson",
      data: {
        type: "Feature",
        properties: {},
        geometry: { type: "Polygon", coordinates: [ring] },
      },
    });
    const layers = map.getStyle().layers;
    const water = layers.findIndex((layer) => layer.id === "water");
    map.addLayer(
      {
        id: polarIceId,
        type: "fill",
        source: polarIceId,
        // landcover_ice (rgba(224,236,236) at 80 %) over the background.
        paint: { "fill-color": "#e5eeed" },
      },
      water >= 0 ? layers[water + 1]?.id : firstLabelLayer(map),
    );
  };

  // Draw the surface beneath the basemap's labels so place names stay
  // readable on top of the data.
  const firstLabelLayer = (map) =>
    map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;

  const renderSurface = (entry) => {
    const { map } = entry;
    entry.field = buildField(entry);
    entry.visible = clickablePoints(entry);
    entry.version += 1;
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    if (!entry.field) return;
    registerProtocol();
    map.addSource(sourceId, {
      type: "raster",
      tiles: [`${protocol}://${entry.id}/${entry.version}/{z}/{x}/{y}`],
      tileSize,
      minzoom: 0,
      maxzoom: 7,
    });
    map.addLayer(
      {
        id: layerId,
        type: "raster",
        source: sourceId,
        paint: {
          "raster-opacity": surfaceOpacity,
          "raster-resampling": "linear",
          "raster-fade-duration": 0,
        },
      },
      firstLabelLayer(map),
    );
  };

  // Nearest drawn observation to a clicked location, if one is close enough.
  const nearestPoint = (entry, lngLat) => {
    const points = entry.visible || [];
    const cosLatitude = Math.max(0.05, Math.cos((lngLat.lat * Math.PI) / 180));
    const limitKm =
      entry.colorMode === "difference" ? 60 : 40;
    let best = null;
    let bestDistance = Infinity;
    for (const point of points) {
      let dLongitude = Math.abs(Number(point[0]) - lngLat.lng);
      if (dLongitude > 180) dLongitude = 360 - dLongitude;
      const dx = dLongitude * cosLatitude * kmPerDegree;
      const dy = (Number(point[1]) - lngLat.lat) * kmPerDegree;
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = point;
      }
    }
    return best && Math.sqrt(bestDistance) <= limitKm ? best : null;
  };

  const showError = (element, message) => {
    let notice = element.parentElement.querySelector(
      ".antarctic-globe-runtime-error",
    );
    if (!notice) {
      notice = document.createElement("p");
      notice.className =
        "antarctic-globe-runtime-error border-t border-red-200 bg-red-50 p-3 text-xs text-red-700";
      notice.setAttribute("role", "alert");
      element.insertAdjacentElement("afterend", notice);
    }
    notice.textContent = message;
  };

  const clearError = (element) => {
    element.parentElement
      .querySelector(".antarctic-globe-runtime-error")
      ?.remove();
  };

  const setPoints = async (entry, source) => {
    if (!source || source === entry.source) return;
    entry.source = source;
    try {
      const response = await fetch(source, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Point data request failed (${response.status}).`);
      }
      const payload = await response.json();
      if (!Array.isArray(payload.points)) {
        throw new Error("The generated point data is invalid.");
      }
      if (entry.source !== source) return;
      entry.points = payload.points;
      entry.colorMode = payload.color_mode || "measurement";
      // Prefer the robust (95th percentile) colour limit when provided; values
      // beyond it are drawn at full colour.
      entry.maxAbs =
        Number(payload.scale_abs) || Number(payload.max_abs) || 0;
      if (entry.ready) renderSurface(entry);
      clearError(entry.element);
    } catch (error) {
      console.error("Could not load Antarctic globe observations.", error);
      showError(entry.element, "Globe observations could not be loaded. Refresh the globe to retry.");
    }
  };

  const mount = (element) => {
    const source = element.getAttribute("data-globe-source");
    let entry = activeMaps.get(element);
    if (entry) {
      void setPoints(entry, source);
      return;
    }
    if (!source) return;
    if (!window.maplibregl) {
      showError(element, "The globe libraries did not load. Check network access and refresh.");
      return;
    }

    const showObservation = (point) => {
      const [longitude, latitude, , value, dataset, column, units] = point;
      const content = document.createElement("div");
      content.className = "text-xs leading-relaxed";
      const title = document.createElement("strong");
      const details = document.createElement("p");
      details.className = "mt-1";
      if (entry.colorMode === "difference") {
        const [, , difference, meanA, meanB, countA, countB, units] = point;
        title.textContent = "Spatial-cell comparison";
        const periods =
          element.getAttribute("data-globe-periods") || "Selected snapshots";
        details.textContent =
          `${periods}: ${Number(meanB).toLocaleString(undefined, {
            maximumFractionDigits: 4,
          })} − ${Number(meanA).toLocaleString(undefined, {
            maximumFractionDigits: 4,
          })} = ${Number(difference).toLocaleString(undefined, {
            maximumFractionDigits: 4,
          })} ${units}. ${countA} and ${countB} observations in the matched cell. Cell centre ${Number(
            latitude,
          ).toFixed(2)}°, ${Number(longitude).toFixed(2)}°.`;
      } else {
        title.textContent = dataset;
        const snapshot =
          element.getAttribute("data-globe-snapshot") || "Undated";
        const measurement =
          element.getAttribute("data-globe-measurement") || column;
        details.textContent =
          `${measurement} (${column}): ${Number(value).toLocaleString(undefined, {
          maximumFractionDigits: 4,
        })} ${units} · ${Number(latitude).toFixed(3)}°, ${Number(longitude).toFixed(3)}° · ${snapshot}`;
      }
      content.append(title, details);
      new window.maplibregl.Popup({ closeButton: true, maxWidth: "280px" })
        .setLngLat([longitude, latitude])
        .setDOMContent(content)
        .addTo(map);
    };

    const map = new window.maplibregl.Map({
      container: element,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [0, -90],
      zoom: 0,
      minZoom: -0.8,
      maxZoom: 6,
      bearing: 0,
      pitch: 0,
      dragPan: true,
      dragRotate: false,
      touchPitch: false,
      attributionControl: true,
    });
    entry = {
      id: nextEntryId++,
      version: 0,
      element,
      source: "",
      points: [],
      visible: [],
      field: null,
      map,
      ready: false,
    };
    activeMaps.set(element, entry);
    entriesById.set(entry.id, entry);
    map.addControl(new window.maplibregl.NavigationControl(), "top-right");
    map.on("click", (event) => {
      const point = nearestPoint(entry, event.lngLat);
      if (point) showObservation(point);
    });
    map.on("load", () => {
      try {
        map.setProjection({ type: "globe" });
        addPolarIce(map);
        map.jumpTo({ center: [0, -85], zoom: -0.3 });
      } catch (error) {
        console.error("Could not initialize the Antarctic globe projection.", error);
        showError(element, "The Antarctic globe projection could not be initialized. Refresh the page to retry.");
        return;
      }
      entry.ready = true;
      if (entry.points.length) renderSurface(entry);
      void setPoints(entry, element.getAttribute("data-globe-source"));
    });
    map.on("error", (event) => {
      if (!map.loaded()) {
        console.error("Could not load the Antarctic basemap.", event.error);
        showError(element, "The coastline basemap could not be loaded. Check network access and refresh.");
      }
    });
  };

  const sync = () => {
    // Reflex replaces component DOM during navigation; dispose removed maps
    // before mounting any newly rendered globe elements.
    for (const [element, entry] of activeMaps) {
      if (!element.isConnected) {
        entry.map.remove();
        activeMaps.delete(element);
        entriesById.delete(entry.id);
      }
    }
    document
      .querySelectorAll("[data-globe-source]")
      .forEach((element) => mount(element));
  };

  const observer = new MutationObserver(sync);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-globe-source"],
    childList: true,
    subtree: true,
  });
  sync();
})();
