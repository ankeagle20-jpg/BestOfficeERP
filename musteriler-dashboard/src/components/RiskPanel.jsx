import React from 'react'

export default function RiskPanel({ riskPuan = 100, enRiskli5 = [], sozlesme30 = [] }) {
  return (
    <div className="glass-card flex min-w-0 flex-col overflow-hidden p-4">
      <h3 className="mb-2 flex items-center gap-1.5 border-b border-white/10 pb-2 text-[13px] font-semibold text-white">
        <i className="fa fa-shield-halved text-cool-blue" /> Risk Paneli
      </h3>
      <div
        className="hexagon mx-auto mb-2 flex h-16 w-14 flex-shrink-0 items-center justify-center rounded-none border border-white/10 text-[1.35rem] font-extrabold text-[#e0f7fa]"
        style={{
          background: 'linear-gradient(165deg, rgba(30,58,80,0.8) 0%, rgba(15,37,55,0.9) 100%)',
          boxShadow: '0 0 20px rgba(79,195,247,0.4), inset 0 0 20px rgba(79,195,247,0.08)',
        }}
      >
        {riskPuan}
      </div>
      <p className="mb-1 text-[10px] text-[#90a4ae] opacity-70">En Riskli 5 Müşteri</p>
      <ul className="max-h-[72px] list-none overflow-y-auto p-0 text-[10px]">
        {enRiskli5.length ? enRiskli5.map((r) => (
          <li key={r.musteri_id} className="flex items-center justify-between gap-1.5 border-b border-white/10 py-1">
            <a href={`#/musteri/${r.musteri_id}`} className="text-cool-blue no-underline hover:underline">{r.name}</a>
            <span className="text-red-500">{r.geciken_gun} Gün</span>
          </li>
        )) : <li className="text-[#90a4ae]">Kayıt yok</li>}
      </ul>
      <p className="mt-2 mb-1 text-[10px] text-[#90a4ae] opacity-70">Sözleşmesi 30 Gün içinde Bitecekler</p>
      <ul className="max-h-[56px] list-none overflow-y-auto p-0 text-[10px]">
        {sozlesme30.length ? sozlesme30.map((s) => (
          <li key={s.musteri_id} className="flex items-center justify-between gap-1.5 border-b border-white/10 py-1">
            <a href={`#/musteri/${s.musteri_id}`} className="text-cool-blue no-underline hover:underline">{s.name}</a>
            <span>{s.office_code} · {s.kalan_gun} gün</span>
          </li>
        )) : <li className="text-[#90a4ae]">Yok</li>}
      </ul>
      <p className="mt-2 mb-1 text-[10px] text-[#90a4ae] opacity-70">TÜFE Artışı Yaklaşanlar</p>
      <ul className="max-h-10 list-none overflow-y-auto p-0 text-[10px]">
        <li className="text-[#90a4ae]">Yok</li>
      </ul>
    </div>
  )
}
