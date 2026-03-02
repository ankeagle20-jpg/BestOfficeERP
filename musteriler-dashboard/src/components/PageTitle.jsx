import React from 'react'

export default function PageTitle() {
  return (
    <div className="glass-card flex flex-row flex-wrap items-center justify-between gap-2 p-3 px-4">
      <div className="flex flex-col gap-0.5">
        <h1 className="text-xl font-bold text-white tracking-wide">Müşteriler</h1>
        <p className="text-xs font-medium text-[#90a4ae] opacity-70">Müşteri Özeti</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[10px] text-[#90a4ae] opacity-80">
          <i className="fa fa-lock text-neon-green" /> Uçtan Uca Şifreli
        </span>
        <select className="rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.6)] px-2.5 py-1.5 text-[11px] text-[#e0f7fa]">
          <option>2026</option>
        </select>
        <select className="rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.6)] px-2.5 py-1.5 text-[11px] text-[#e0f7fa]">
          <option>Son 6 Ay</option>
        </select>
        <button type="button" className="flex h-8 w-8 items-center justify-center rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.6)] text-[#90a4ae] transition hover:border-cool-blue/40 hover:text-cool-blue" title="Takvim">
          <i className="fa fa-calendar-alt" />
        </button>
        <button type="button" className="flex h-8 w-8 items-center justify-center rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.6)] text-[#90a4ae] transition hover:border-cool-blue/40 hover:text-cool-blue" title="Ayarlar">
          <i className="fa fa-cog" />
        </button>
        <a href="/" className="flex h-8 w-8 items-center justify-center rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.6)] text-[#90a4ae] transition hover:border-cool-blue/40 hover:text-cool-blue" title="Ana Sayfa">
          <i className="fa fa-home" />
        </a>
      </div>
    </div>
  )
}
