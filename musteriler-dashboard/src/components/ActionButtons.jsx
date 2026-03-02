import React from 'react'

const bigButtons = [
  { label: 'Toplu Faturalama', icon: 'fa-file-invoice', badge: 20 },
  { label: 'Toplu Tahsilat', icon: 'fa-wallet', badge: 11 },
  { label: 'Kargolar', icon: 'fa-box', badge: 6 },
  { label: 'Müşteri Listesi', icon: 'fa-list', badge: 120 },
]

const smallButtons = [
  { label: 'Kefillik Analizi', icon: 'fa-users' },
  { label: 'Risk Analizi', icon: 'fa-cube' },
  { label: 'TÜFE Simulasyonu', icon: 'fa-chart-line' },
  { label: 'WhatsApp Toplu Gönder', icon: 'fa-whatsapp' },
  { label: 'Excel', icon: 'fa-file-excel' },
  { label: 'AI Yorumla', icon: 'fa-robot' },
]

const smallBtnClasses = [
  'from-blue-900 to-blue-700',
  'from-red-900 to-red-700',
  'from-orange-900 to-persimmon',
  'from-green-900 to-neon-green',
  'from-cyan-900 to-cyan-400',
  'from-purple-900 to-purple-600',
]

export default function ActionButtons() {
  return (
    <div className="glass-card flex flex-col gap-3 p-4">
      <h3 className="flex items-center gap-1.5 border-b border-white/10 pb-2 text-[13px] font-semibold text-white">
        <i className="fa fa-bolt text-cool-blue" /> Toplu Faturalama
      </h3>
      <div className="grid grid-cols-2 min-[500px]:grid-cols-4 gap-fintech-gap">
        {bigButtons.map((b) => (
          <button
            key={b.label}
            type="button"
            className="relative flex min-w-0 flex-col items-center justify-center gap-2 rounded-fintech-sm border border-white/20 bg-gradient-to-br from-blue-900 to-blue-700 p-2.5 text-center text-[0.85rem] font-semibold text-white transition hover:-translate-y-0.5 hover:shadow-glass-hover"
          >
            {b.badge != null && (
              <span className="absolute right-2 top-1.5 rounded-full bg-amber-400 px-1.5 py-0.5 text-[9px] font-bold text-deep-midnight">
                {b.badge}
              </span>
            )}
            <span className="flex items-center justify-center text-lg"><i className={'fa ' + b.icon} /></span>
            <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap">{b.label}</span>
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 min-[400px]:grid-cols-3 gap-fintech-gap">
        {smallButtons.map((b, i) => (
          <a
            key={b.label}
            href="#"
            className={`flex min-w-0 flex-col items-center justify-center gap-2 rounded-fintech-sm border border-white/20 bg-gradient-to-br p-2.5 text-center text-[0.85rem] font-semibold text-white no-underline transition hover:-translate-y-0.5 hover:shadow-glass-hover ${smallBtnClasses[i]}`}
          >
            <span className="flex items-center justify-center text-base"><i className={'fa ' + b.icon} /></span>
            <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap">{b.label}</span>
          </a>
        ))}
      </div>
    </div>
  )
}
