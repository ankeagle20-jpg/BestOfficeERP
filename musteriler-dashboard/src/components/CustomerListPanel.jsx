import React from 'react'

function formatBorc(n) {
  return Number(n).toLocaleString('tr-TR', { minimumFractionDigits: 2 }) + ' ₺'
}

export default function CustomerListPanel({ musteriler = [], toplamBakiye = 0 }) {
  return (
    <div className="glass-card flex h-full min-h-0 w-full max-w-[400px] flex-col overflow-hidden">
      <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-[#1e3a50] px-3 py-2">
        <h2 className="truncate text-sm font-bold text-white">Müşteri Listesi</h2>
        <div className="flex items-center gap-2">
          <a href="#" className="inline-flex items-center gap-1.5 rounded-fintech-sm border border-white/15 bg-gradient-to-br from-green-800 to-green-700 px-2.5 py-1.5 text-[11px] font-semibold text-white no-underline">
            <i className="fa fa-file-excel" /> Excel
          </a>
          <button type="button" className="text-[#90a4ae] hover:text-white" aria-label="Kapat">×</button>
        </div>
      </div>
      <div className="flex flex-1 min-h-0 flex-col p-2">
        <div className="mb-2 flex items-center gap-1.5">
          <i className="fa fa-search text-[10px] text-[#90a4ae]" />
          <input type="text" placeholder="Q Ad/Ünvan" className="flex-1 rounded-md border border-[#2d4060] bg-[#1e3a50] px-2 py-1.5 text-[11px] text-[#e0f7fa]" />
          <select className="rounded border border-[#2d4060] bg-[#1e3a50] px-1.5 py-1 text-[10px] text-[#e0f7fa]">
            <option>Ad/Ünvan</option>
          </select>
        </div>
        <p className="mb-1.5 text-[10px] text-[#90a4ae] opacity-70">Ad/Ünvan: {musteriler.length}</p>
        <div className="scroll-panel flex-1 min-h-0 overflow-y-auto overflow-x-auto rounded-fintech-sm border border-[#1e3a50] bg-[rgba(15,37,55,0.6)] p-2">
          <table className="w-full table-fixed border-collapse text-[11px]">
            <thead>
              <tr>
                <th className="max-w-0 truncate border-b border-[#1e3a50] bg-[#1e3a50] px-2 py-2 text-left text-[10px] font-semibold text-cool-blue">Ad/Ünvan</th>
                <th className="whitespace-nowrap border-b border-[#1e3a50] bg-[#1e3a50] px-2 py-2 text-left text-[10px] font-semibold text-cool-blue">Ofis/Ünite</th>
                <th className="whitespace-nowrap border-b border-[#1e3a50] bg-[#1e3a50] px-2 py-2 text-left text-[10px] font-semibold text-cool-blue">Borç</th>
                <th className="whitespace-nowrap border-b border-[#1e3a50] bg-[#1e3a50] px-2 py-2 text-left text-[10px] font-semibold text-cool-blue"><i className="fa fa-sort mr-0.5" /> Gecikme</th>
              </tr>
            </thead>
            <tbody>
              {musteriler.map((m) => (
                <tr key={m.id} className="cursor-pointer border-b border-[#1e3a50] transition hover:scale-[1.01] hover:bg-[rgba(30,58,80,0.5)] hover:shadow-[0_0_12px_rgba(79,195,247,0.25)]">
                  <td className="max-w-0 truncate px-2 py-2 text-[#e0f7fa]" title={m.name}>{m.name || '—'}</td>
                  <td className="truncate px-2 py-2 text-[#e0f7fa]">{m.office_code || '—'}</td>
                  <td className={`px-2 py-2 font-semibold ${(m.toplam_borc || 0) > 0 ? 'text-persimmon' : 'text-cool-blue'}`}>{formatBorc(m.toplam_borc ?? 0)}</td>
                  <td className={`px-2 py-2 ${(m.geciken_gun || 0) === 0 ? 'text-green-400' : 'text-red-500'}`}>{m.geciken_gun ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2 flex flex-shrink-0 flex-wrap items-center justify-between gap-2 border-t border-[#1e3a50] pt-2 text-[10px] text-[#90a4ae]">
          <div className="flex items-center gap-2">
            <i className="fa fa-search cursor-pointer hover:text-cool-blue" />
            <i className="fa fa-plus cursor-pointer hover:text-cool-blue" />
            <i className="fa fa-chevron-left cursor-pointer hover:text-cool-blue" />
            <i className="fa fa-chevron-right cursor-pointer hover:text-cool-blue" />
            <select className="rounded border border-[#2d4060] bg-[#1e3a50] px-1 py-0.5 text-[10px] text-[#e0f7fa]"><option>Aktif M</option></select>
            <span>{musteriler.length}</span>
          </div>
          <div className="flex flex-col items-end">
            <span className="opacity-70">Toplam Müşteri <strong className="text-[#e0f7fa]">{musteriler.length}</strong></span>
            <span className="opacity-70">₺ <strong className="text-[#e0f7fa]">{Number(toplamBakiye).toLocaleString('tr-TR')}</strong></span>
          </div>
        </div>
        <button type="button" className="mt-2 w-full rounded-fintech-sm border-0 bg-gradient-to-r from-blue-900 to-blue-700 py-1.5 text-[11px] font-semibold text-white">
          Müşteri Listesine Git →
        </button>
      </div>
    </div>
  )
}
