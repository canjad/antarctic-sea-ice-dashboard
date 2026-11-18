(() => {
  // Keep the map colors and overlays local to this runtime so multiple map
  // elements can be mounted and disposed independently.
  const activeMaps = new Map();
  // Keep observations just above the globe surface. A large altitude adds
  // parallax that shifts points off their cells and over coastlines.
  const observationAltitudeMeters = 1500;
  // Source grids use 25 km cells; a radius of half a cell keeps neighbouring
  // points from bleeding across coastlines.
  const cellRadiusMeters = 14500;
  // Concentrations below the conventional 15 % ice-edge threshold fade into
  // the ocean so the ice edge does not end in a hard dark rim.
  const iceEdgePercent = 15;

  // Width, in degrees of latitude, of the soft fade at the grid's outer edge.
  const edgeFadeDegrees = 4;
  // Difference markers are 0.5° latitude × 1° longitude bins.
  const binLatitudeKm = 55.6;
  const binLongitudeKmAtEquator = 111.3;

  // The source data is a rectangular polar-stereographic grid. On a round
  // globe its corners read as a square frame, so find the circle that fits
  // inside the grid (the southernmost of the grid's northern edges) and fade
  // the layer out towards it.
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
  // of the rectangular source grid. Drawing it paints a square over the
  // ocean, so let the basemap's ocean show through instead.
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
  // which never reaches the grid edge and is already free of the square).
  const edgeFade = (entry, point) => {
    if (entry.edgeLatitude === null || isConcentrationPoint(entry, point)) {
      return 1;
    }
    return Math.max(
      0,
      Math.min(1, (entry.edgeLatitude - Number(point[1])) / edgeFadeDegrees),
    );
  };

  const measurementColor = (entry, point) => {
    const color = colorAt(point[2]);
    let fade = edgeFade(entry, point);
    if (entry.isConcentration) {
      fade *= Math.min(1, Number(point[3]) / iceEdgePercent);
    }
    color[3] = Math.round(color[3] * fade);
    return color;
  };

  const differenceColor = (entry, point) => {
    const color = differenceColorAt(point[2], entry.maxAbs);
    color[3] = Math.round(color[3] * edgeFade(entry, point));
    return color;
  };

  // Size each difference marker to its bin, which narrows towards the pole,
  // so markers next to the coast do not spill onto land.
  const differenceRadius = (point) => {
    const longitudeKm =
      binLongitudeKmAtEquator *
      Math.max(0.05, Math.cos((Number(point[1]) * Math.PI) / 180));
    return 550 * Math.sqrt(binLatitudeKm * longitudeKm);
  };

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
    if (upper <= 0) return [...stops[0][1], 230];
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
      Math.round(235 * Math.max(0.3, Math.min(1, amount * 2.5))),
    ];
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
      if (entry.overlay) {
        entry.overlay.setProps({ layers: [entry.layer(visiblePoints(entry))] });
      }
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
    if (!window.maplibregl || !window.deck?.ScatterplotLayer || !window.deck?.MapboxOverlay) {
      showError(element, "The globe libraries did not load. Check network access and refresh.");
      return;
    }

    const showObservation = (info) => {
      if (!info.object) return;
      const [longitude, latitude, , value, dataset, column, units] =
        info.object;
      const content = document.createElement("div");
      content.className = "text-xs leading-relaxed";
      const title = document.createElement("strong");
      const details = document.createElement("p");
      details.className = "mt-1";
      if (entry.colorMode === "difference") {
        const [
          ,
          ,
          difference,
          meanA,
          meanB,
          countA,
          countB,
          units,
        ] = info.object;
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

    const layer = (points) =>
      new window.deck.ScatterplotLayer({
        id: "antarctic-sea-ice-observations",
        data: points,
        getPosition: (point) => [
          point[0],
          point[1],
          observationAltitudeMeters,
        ],
        getFillColor: (point) =>
          entry.colorMode === "difference"
            ? differenceColor(entry, point)
            : measurementColor(entry, point),
        getRadius:
          entry.colorMode === "difference"
            ? differenceRadius
            : cellRadiusMeters,
        radiusUnits: "meters",
        radiusMinPixels: 0.75,
        radiusMaxPixels: 40,
        opacity: 0.96,
        antialiasing: true,
        pickable: true,
        autoHighlight: true,
        onClick: showObservation,
        stroked: false,
      });

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
    const overlay = new window.deck.MapboxOverlay({
      interleaved: true,
      layers: [],
    });
    entry = { element, source: "", points: [], layer, map, overlay };
    activeMaps.set(element, entry);
    map.addControl(new window.maplibregl.NavigationControl(), "top-right");
    map.addControl(overlay);
    map.on("load", () => {
      try {
        map.setProjection({ type: "globe" });
        map.jumpTo({ center: [0, -85], zoom: -0.3 });
      } catch (error) {
        console.error("Could not initialize the Antarctic globe projection.", error);
        showError(element, "The Antarctic globe projection could not be initialized. Refresh the page to retry.");
        return;
      }
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
        entry.overlay.finalize();
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
