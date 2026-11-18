(() => {
  // Render uploaded observations as one smooth colour surface on a MapLibre
  // globe. Points are blended with a Gaussian kernel into a Web Mercator
  // image, so neighbouring cells merge into a continuous field (no per-cell
  // dots or squares) and the field fades out softly where data ends.
  const activeMaps = new Map();

  const sourceId = "antarctic-observation-surface";
  const layerId = "antarctic-observation-surface";

  // Raster resolution in degrees of longitude (and Mercator latitude) per
  // pixel. 0.2° is ~5–20 km over the Southern Ocean, finer than the 25 km
  // source grid, so the blended surface looks continuous.
  const degreesPerPixel = 0.2;
  // MapLibre's Mercator world stops at ±85.0511°; data south of it is all
  // inland Antarctica, which the source grids do not cover.
  const southernLimit = -85;
  // Gaussian width relative to the spacing of the source cells. With
  // sigma = 0.75 × spacing the kernels overlap enough to read as a smooth
  // field while still keeping local detail.
  const kernelSpacingRatio = 0.75;
  const fullCoverage = 2 * Math.PI * kernelSpacingRatio ** 2;
  const surfaceOpacity = 0.88;

  // Concentrations below the conventional 15 % ice-edge threshold fade into
  // the ocean so the ice edge does not end in a hard rim.
  const iceEdgePercent = 15;
  // Width, in degrees of latitude, of the soft fade at the grid's outer edge.
  const edgeFadeDegrees = 4;
  // Source cell sizes: the NSIDC polar grid is 25 km; comparison bins are
  // 0.5° latitude × 1° longitude.
  const gridCellKm = 25;
  const binLatitudeKm = 55.6;
  const kmPerDegree = 111.32;

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
    if (upper < 0) return [...stops[stops.length - 1][1], 230];
    if (upper === 0) return [...stops[0][1], 230];
    const [lowValue, lowColor] = stops[upper - 1];
    const [highValue, highColor] = stops[upper];
    const amount = (value - lowValue) / (highValue - lowValue);
    return [
      ...lowColor.map((channel, index) =>
        Math.round(channel + (highColor[index] - channel) * amount),
      ),
      230,
    ];
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
      Math.round(235 * Math.max(0.1, Math.min(1, amount * 2.5))),
    ];
  };

  // The source data is a rectangular polar-stereographic grid. On a round
  // globe its corners read as a square frame, so find the circle that fits
  // inside the grid (the southernmost of the grid's northern edges) and fade
  // the surface out towards it.
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

  // Open water (0 % concentration, or 0 % in both compared snapshots) is most
  // of the rectangular source grid; leave it to the basemap's ocean.
  const visiblePoints = (entry) => {
    const measurement =
      entry.element.getAttribute("data-globe-measurement") || "";
    entry.isConcentration =
      entry.colorMode === "measurement" && /concentration/i.test(measurement);
    entry.edgeLatitude = gridEdgeLatitude(entry.points);
    return entry.points.filter((point) => {
      if (isConcentrationPoint(entry, point)) {
        return entry.colorMode === "difference"
          ? Number(point[3]) > 0 || Number(point[4]) > 0
          : Number(point[3]) > 0;
      }
      return (
        entry.edgeLatitude === null || Number(point[1]) < entry.edgeLatitude
      );
    });
  };

  // 0–1 opacity factor for the soft circular edge (not applied to sea ice,
  // which never reaches the grid edge).
  const edgeFade = (entry, point) => {
    if (entry.edgeLatitude === null || isConcentrationPoint(entry, point)) {
      return 1;
    }
    return Math.max(
      0,
      Math.min(1, (entry.edgeLatitude - Number(point[1])) / edgeFadeDegrees),
    );
  };

  // Web Mercator y in "degrees" (same units as longitude).
  const mercatorY = (latitude) =>
    (Math.log(Math.tan(Math.PI / 4 + (latitude * Math.PI) / 360)) * 180) /
    Math.PI;

  // Size of one source cell, in raster pixels, at a latitude.
  const cellSpacingPixels = (entry, latitude) => {
    const kmPerPixel =
      degreesPerPixel *
      kmPerDegree *
      Math.max(0.05, Math.cos((latitude * Math.PI) / 180));
    if (entry.colorMode === "difference") {
      const longitudeKm =
        kmPerDegree * Math.max(0.05, Math.cos((latitude * Math.PI) / 180));
      return Math.sqrt(binLatitudeKm * longitudeKm) / kmPerPixel;
    }
    return gridCellKm / kmPerPixel;
  };

  const smoothstep = (low, high, value) => {
    const t = Math.max(0, Math.min(1, (value - low) / (high - low)));
    return t * t * (3 - 2 * t);
  };

  // Blend points into an RGBA image covering longitude −180…180 and
  // latitude northernLimit…southernLimit in Web Mercator.
  const buildSurface = (entry, points) => {
    if (!points.length) return null;
    let northernLimit = southernLimit;
    for (const point of points) {
      northernLimit = Math.max(northernLimit, Number(point[1]));
    }
    northernLimit = Math.min(80, northernLimit + 3);
    const yTop = mercatorY(northernLimit);
    const yBottom = mercatorY(southernLimit);
    const width = Math.round(360 / degreesPerPixel);
    const height = Math.max(1, Math.round((yTop - yBottom) / degreesPerPixel));

    const weight = new Float32Array(width * height);
    const weightedValue = new Float32Array(width * height);
    const weightedRaw = new Float32Array(width * height);
    const weightedFade = new Float32Array(width * height);

    for (const point of points) {
      const longitude = Number(point[0]);
      const latitude = Number(point[1]);
      const value = Number(point[2]);
      const raw = Number(point[3]);
      const fade = edgeFade(entry, point);
      if (fade <= 0) continue;
      const cx = (longitude + 180) / degreesPerPixel;
      const cy = (yTop - mercatorY(latitude)) / degreesPerPixel;
      const sigma = Math.max(0.6, kernelSpacingRatio * cellSpacingPixels(entry, latitude));
      const radius = Math.ceil(2.5 * sigma);
      const inverse = 1 / (2 * sigma * sigma);
      const y0 = Math.max(0, Math.floor(cy - radius));
      const y1 = Math.min(height - 1, Math.ceil(cy + radius));
      for (let y = y0; y <= y1; y += 1) {
        const dy = y + 0.5 - cy;
        for (let x = Math.floor(cx - radius); x <= Math.ceil(cx + radius); x += 1) {
          const dx = x + 0.5 - cx;
          const w = Math.exp(-(dx * dx + dy * dy) * inverse);
          if (w < 0.01) continue;
          // Wrap across the antimeridian.
          const index = y * width + (((x % width) + width) % width);
          weight[index] += w;
          weightedValue[index] += w * value;
          weightedRaw[index] += w * raw;
          weightedFade[index] += w * fade;
        }
      }
    }

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    const image = context.createImageData(width, height);
    const pixels = image.data;
    for (let index = 0; index < weight.length; index += 1) {
      const w = weight[index];
      if (w <= 0.02) continue;
      const value = weightedValue[index] / w;
      let color;
      let alpha;
      if (entry.colorMode === "difference") {
        color = differenceColorAt(value, entry.maxAbs);
        alpha = color[3] / 255;
      } else {
        color = colorAt(value);
        alpha = color[3] / 255;
        if (entry.isConcentration) {
          alpha *= smoothstep(2, iceEdgePercent + 5, weightedRaw[index] / w);
        }
      }
      // Soft edge where data coverage ends (coastlines, open water, grid edge).
      alpha *= smoothstep(0.15, 0.7, w / fullCoverage);
      alpha *= weightedFade[index] / w;
      const offset = index * 4;
      pixels[offset] = color[0];
      pixels[offset + 1] = color[1];
      pixels[offset + 2] = color[2];
      pixels[offset + 3] = Math.round(255 * alpha);
    }
    context.putImageData(image, 0, 0);
    return {
      url: canvas.toDataURL("image/png"),
      coordinates: [
        [-180, northernLimit],
        [180, northernLimit],
        [180, southernLimit],
        [-180, southernLimit],
      ],
    };
  };

  // Draw the surface beneath the basemap's labels so place names stay
  // readable on top of the data.
  const firstLabelLayer = (map) =>
    map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;

  const renderSurface = (entry) => {
    const { map } = entry;
    const points = visiblePoints(entry);
    entry.visible = points;
    const surface = buildSurface(entry, points);
    const existing = map.getSource(sourceId);
    if (!surface) {
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (existing) map.removeSource(sourceId);
      return;
    }
    if (existing) {
      existing.updateImage(surface);
      return;
    }
    map.addSource(sourceId, { type: "image", ...surface });
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
      entry.colorMode === "difference" ? 60 : gridCellKm * 1.5;
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
    entry = { element, source: "", points: [], visible: [], map, ready: false };
    activeMaps.set(element, entry);
    map.addControl(new window.maplibregl.NavigationControl(), "top-right");
    map.on("click", (event) => {
      const point = nearestPoint(entry, event.lngLat);
      if (point) showObservation(point);
    });
    map.on("load", () => {
      try {
        map.setProjection({ type: "globe" });
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
