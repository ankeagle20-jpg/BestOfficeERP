import React from 'react'

export default function FooterStrip({ toplamMusteri = 0, toplamBakiye = 0 }) {
  return (
    <div className="glass-card flex flex-wrap items-center justify-between gap-2 p-3 px-4">
      <div className="flex flex-wrap items-center gap-2">
        <a href="#" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#90a4ae] no-underline transition hover:border-cool-blue/40 hover:text-cool-blue hover:shadow-[0_0_12px_rgba(79,195,247,0.2)]">
          <i className="fa fa-chart-pie" /> Kârlılık
        </a>
        <a href="#" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#90a4ae] no-underline transition hover:border-cool-blue/40 hover:text-cool-blue hover:shadow-[0_0_12px_rgba(79,195,247,0.2)]">
          <i className="fa fa-exclamation-circle" /> Risk
        </a>
        <a href="/tufe" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#90a4ae] no-underline transition hover:border-cool-blue/40 hover:text-cool-blue hover:shadow-[0_0_12px_rgba(79,195,247,0.2)]">
          <i className="fa fa-building" /> TÜFE
        </a>
        <a href="#" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#90a4ae] no-underline transition hover:border-cool-blue/40 hover:text-cool-blue hover:shadow-[0_0_12px_rgba(79,195,247,0.2)]">
          <i className="fa fa-whatsapp" /> WhatsApp
        </a>
        <a href="#" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/10 bg-[rgba(30,58,80,0.5)] px-2.5 py-1.5 text-[11px] text-[#90a4ae] no-underline transition hover:border-cool-blue/40 hover:text-cool-blue hover:shadow-[0_0_12px_rgba(79,195,247,0.2)]">
          <i className="fa fa-file-excel" /> Excel
        </a>
      </div>
      <div className="flex flex-wrap items-center gap-fintech-gap">
        <button
          type="button"
          className="rounded-fintech-sm border-0 bg-gradient-to-r from-green-800 to-neon-green px-4 py-2 text-sm font-semibold text-white shadow-[0_0_12px_rgba(205,220,57,0.35)] transition hover:from-green-700 hover:to-neon-green"
        >
          Müşteri Listesine Git →
        </button>
        <div className="flex flex-col items-end text-[11px] text-[#90a4ae] opacity-80">
          <span>{toplamMusteri} Toplam Müşteri</span>
          <span className="mt-0.5 text-sm font-bold text-[#e0f7fa]">
            {Number(toplamBakiye).toLocaleString('tr-TR')} ₺
          </span>
        </div>
      </div>
    </div>
  )
}
