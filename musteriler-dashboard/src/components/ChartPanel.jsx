import React from 'react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const v = payload[0].value
  const str = typeof v === 'number' ? v.toLocaleString('tr-TR', { minimumFractionDigits: 2 }) : v
  return (
    <div className="rounded-lg border border-white/10 bg-[rgba(15,37,55,0.9)] px-3 py-2 shadow-lg">
      <p className="text-xs text-cool-blue">{label}</p>
      <p className="text-sm font-semibold text-[#e0f7fa]">Tahsilat: {str} ₺</p>
    </div>
  )
}

export default function ChartPanel({ data }) {
  const chartData = (data || []).map((d) => ({ name: d.ay + ' ' + d.yil, value: d.tutar }))

  return (
    <div className="glass-card flex min-h-0 flex-1 flex-col overflow-hidden p-4">
      <h3 className="mb-2 flex items-center gap-1.5 border-b border-white/10 pb-2 text-[13px] font-semibold text-white">
        <i className="fa fa-bolt text-cool-blue" /> Müşteri Analizi
      </h3>
      <div className="mb-2 flex gap-2">
        <select className="rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#e0f7fa]">
          <option>Euro ₺</option>
        </select>
        <select className="rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#e0f7fa]">
          <option>Son 6 Ay</option>
        </select>
      </div>
      <div className="min-h-0 flex-1" style={{ minHeight: 100, maxHeight: 140 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="lineGradMusteri" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#4fc3f7" />
                <stop offset="60%" stopColor="rgba(79,195,247,0.7)" />
                <stop offset="100%" stopColor="#cddc39" />
              </linearGradient>
              <linearGradient id="areaGradMusteri" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stopColor="rgba(79,195,247,0.08)" />
                <stop offset="50%" stopColor="rgba(205,220,57,0.06)" />
                <stop offset="100%" stopColor="rgba(205,220,57,0.12)" />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="0" stroke="#1e3a50" />
            <XAxis dataKey="name" tick={{ fill: '#90a4ae', fontSize: 9 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#90a4ae', fontSize: 10 }} axisLine={false} tickLine={false} width={32} />
            <Tooltip content={<CustomTooltip />} />
            <Area type="monotone" dataKey="value" stroke="url(#lineGradMusteri)" strokeWidth={2} fill="url(#areaGradMusteri)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
