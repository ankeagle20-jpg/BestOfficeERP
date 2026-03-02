import React from 'react'

const cards = [
  { key: 'toplam', val: 'toplam_musteri', lbl: 'Toplam Müşteri', icon: 'fa-users', accent: 'blue', hero: true },
  { key: 'aktif', val: 'aktif_musteri', lbl: 'Aktif Müşteri', icon: 'fa-user-check', accent: 'green' },
  { key: 'kritik', val: 'kritik_musteri', lbl: 'Kritik Müşteri', icon: 'fa-exclamation-triangle', accent: 'orange', hero: true },
  { key: 'tahakkuk', val: 'toplam_aylik_tahakkuk', lbl: 'Toplam Aylık Tahakkuk', icon: 'fa-lira-sign', accent: 'cyan', format: '₺' },
  { key: 'tahsilat', val: 'tahsilat_orani', lbl: 'Tahsilat Oranı %', icon: 'fa-percent', accent: 'blue', hero: true, format: '%' },
]

const accentClasses = {
  blue: 'text-cool-blue',
  green: 'text-neon-green',
  orange: 'text-persimmon',
  cyan: 'text-cyan-400',
}

export default function KpiStrip({ kpi }) {
  return (
    <div className="grid min-w-0 grid-cols-2 gap-fintech-gap sm:grid-cols-3 lg:grid-cols-5">
      {cards.map(({ key, val, lbl, icon, accent, hero, format }) => {
        const v = kpi[val] ?? 0
        const display = format === '₺' ? `${Number(v).toLocaleString('tr-TR')} ₺` : format === '%' ? `${v}%` : v
        return (
          <div
            key={key}
            className="glass-card relative flex min-w-0 cursor-pointer flex-col items-center justify-center p-3 text-center transition hover:-translate-y-0.5 hover:shadow-glass-hover"
            role="button"
            tabIndex={0}
          >
            <span className="absolute right-2 top-1.5 text-[10px] text-neon-green opacity-90">
              <i className="fa fa-arrow-up" />
            </span>
            <div className={`mb-0.5 flex items-center justify-center text-lg ${accentClasses[accent]}`}>
              <i className={`fa ${icon}`} />
            </div>
            <div className={`font-bold text-[#e0f7fa] leading-tight ${hero ? 'text-[1.2rem]' : 'text-base'}`}>
              {display}
            </div>
            <div className={`mt-0.5 text-[10px] font-medium text-[#90a4ae] ${hero ? 'opacity-100' : 'opacity-70'}`}>
              {lbl}
            </div>
          </div>
        )
      })}
    </div>
  )
}
