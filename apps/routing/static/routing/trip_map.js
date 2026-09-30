(function () {
  const container = document.getElementById("map");
  const plan = JSON.parse(document.getElementById("plan-data").textContent);
  const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const map = L.map(container);
  L.tileLayer(container.dataset.tileUrl, {
    maxZoom: 18,
    attribution: esc(container.dataset.tileAttribution),
  }).addTo(map);

  const line = L.geoJSON(plan.route.geometry, { style: { color: "#0b6e4f", weight: 5, opacity: 0.8 } }).addTo(map);
  map.fitBounds(line.getBounds(), { padding: [30, 30] });

  const pin = (kind) => L.divIcon({ className: `pin pin-${kind}`, iconSize: [22, 22], iconAnchor: [11, 11] });
  L.marker([plan.start.latitude, plan.start.longitude], { icon: pin("start") }).addTo(map).bindPopup(`<b>Start</b><br>${esc(plan.start.label)}`);
  L.marker([plan.finish.latitude, plan.finish.longitude], { icon: pin("finish") }).addTo(map).bindPopup(`<b>Finish</b><br>${esc(plan.finish.label)}`);

  plan.fuel.stops.forEach((stop) => {
    const icon = L.divIcon({ className: "stop-pin", html: String(stop.stop_number), iconSize: [26, 26], iconAnchor: [13, 13] });
    const approximate = stop.station.location_precision === "city_centroid" ? "<br><i>Shown at the city centre</i>" : "";
    L.marker([stop.station.latitude, stop.station.longitude], { icon }).addTo(map).bindPopup(
      `<b>${stop.stop_number}. ${esc(stop.station.name)}</b><br>${esc(stop.station.address)}<br>${esc(stop.station.city)}, ${esc(stop.station.state)}` +
      `<br>Mile ${stop.mile_marker} · $${stop.price_per_gallon}/gal<br>${stop.gallons} gal · $${stop.cost_usd}${approximate}`
    );
  });
})();
