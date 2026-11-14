import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useState } from 'react'
import { Circle, CircleMarker, MapContainer, Polyline, Rectangle, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { Meta, Rfi, TractorStatus } from '../types'
import { TRACTOR_COLOR } from '../format'


function FlyTo({ target }: { target: [number, number] | null }) {
  const map = useMap()
  useEffect(() => { if (target) map.flyTo(target, 17, { duration: 0.8 }) }, [map, target])
  return null
}

function Tiles() {
  // satellite tiles need internet; on tile errors the drawn field outline below is the fallback
  const [failed, setFailed] = useState(false)
  if (failed) return null
  return (
    <TileLayer
      url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
      attribution="Imagery © Esri" maxZoom={19}
      eventHandlers={{ tileerror: () => setFailed(true) }}
    />
  )
}

export function FarmMap({ meta, tractors, trails, rfi, alarmed, focus, showSites }: {
  meta: Meta | null
  tractors: TractorStatus[]
  trails: Record<string, [number, number][]>
  rfi: Rfi[]
  alarmed: Set<string>
  focus: [number, number] | null
  showSites: boolean
}) {
  // the three per-tractor boxes are identical in this dataset (one shared field), so draw distinct ones only
  const fields = useMemo(() => {
    const seen = new Set<string>()
    return (meta?.fields ?? []).filter((f) => { const k = `${f.lat}|${f.lon}`; if (seen.has(k)) return false; seen.add(k); return true })
  }, [meta])
  const center: [number, number] = fields[0]
    ? [(fields[0].lat[0] + fields[0].lat[1]) / 2, (fields[0].lon[0] + fields[0].lon[1]) / 2]
    : [46.9036, -97.1993]
  return (
    <MapContainer center={center} zoom={15} className="map" scrollWheelZoom>
      <Tiles />
      {fields.map((f, i) => (
        <Rectangle key={i} bounds={[[f.lat[0], f.lon[0]], [f.lat[1], f.lon[1]]]}
          pathOptions={{ color: '#8fbc8f', weight: 1.5, fillColor: '#3a5a3a', fillOpacity: 0.25 }}>
          <Tooltip sticky>Field outline: {f.source} (no field geometry in the data)</Tooltip>
        </Rectangle>
      ))}
      {showSites && meta?.site_multipath?.sites.map((s, i) => (
        <Rectangle key={`s${i}`} bounds={[[s.lat[0], s.lon[0]], [s.lat[1], s.lon[1]]]}
          pathOptions={{ color: '#b388ff', weight: 0.5, fillOpacity: Math.min(0.15 + (s.rate ?? 0) * 0.5, 0.65) }}>
          <Tooltip sticky>Learned multipath site: {s.dips}/{s.passes} dev passes dipped ({Math.round((s.rate ?? 0) * 100)}%)</Tooltip>
        </Rectangle>
      ))}
      {rfi.filter((r) => r.lat != null && r.lon != null).map((r) => (
        <Circle key={r.id} center={[r.lat!, r.lon!]} radius={r.radius_km * 1000}
          pathOptions={{ color: '#ff5d5d', dashArray: '6 6', weight: 2, fillOpacity: 0.05 }}>
          <Tooltip sticky>{r.id} · conf {r.confidence} · event {r.t_event.slice(5, 16)}Z · available {r.t_avail.slice(5, 16)}Z<br />{r.text}</Tooltip>
        </Circle>
      ))}
      {Object.entries(trails).map(([tr, pts]) => pts.length > 1 && (
        <Polyline key={`t${tr}`} positions={pts} pathOptions={{ color: TRACTOR_COLOR[tr] ?? '#fff', weight: 2, opacity: 0.7 }} />
      ))}
      {tractors.filter((t) => t.lat != null && t.lon != null).map((t) => (
        <CircleMarker key={t.id} center={[t.lat!, t.lon!]} radius={9}
          pathOptions={{ color: alarmed.has(t.id) ? '#ff3b3b' : '#111', weight: alarmed.has(t.id) ? 4 : 2, fillColor: TRACTOR_COLOR[t.id] ?? '#fff', fillOpacity: 1 }}>
          <Tooltip permanent direction="top" offset={[0, -8]}>{t.id}</Tooltip>
        </CircleMarker>
      ))}
      <FlyTo target={focus} />
    </MapContainer>
  )
}
