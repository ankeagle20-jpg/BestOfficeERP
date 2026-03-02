import React from 'react'
import PageTitle from './components/PageTitle'
import KpiStrip from './components/KpiStrip'
import ChartPanel from './components/ChartPanel'
import RiskPanel from './components/RiskPanel'
import ActionButtons from './components/ActionButtons'
import CustomerListPanel from './components/CustomerListPanel'
import FooterStrip from './components/FooterStrip'
import { kpi, tahsilatTrend, enRiskli5, sozlesme30List, musteriler, toplamBakiye } from './data/mock'

export default function App() {
  return (
    <div className="flex h-screen min-h-0 w-full overflow-hidden bg-gradient-to-b from-deep-midnight via-deep-midnight-2 to-deep-midnight-3">
      <div className="flex min-h-0 flex-1 flex-col gap-fintech-gap overflow-hidden p-5">
        <PageTitle />
        <KpiStrip kpi={kpi} />
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-fintech-gap lg:grid-cols-[1fr_260px] items-stretch">
          <div className="flex min-h-0 min-w-0 flex-col">
            <ChartPanel data={tahsilatTrend} />
          </div>
          <RiskPanel riskPuan={100} enRiskli5={enRiskli5} sozlesme30={sozlesme30List} />
        </div>
        <ActionButtons />
        <FooterStrip toplamMusteri={kpi.toplam_musteri} toplamBakiye={toplamBakiye} />
      </div>
      <div className="flex h-full min-h-0 flex-shrink-0 items-stretch gap-fintech-gap pl-0 pr-5 py-5">
        <CustomerListPanel musteriler={musteriler} toplamBakiye={toplamBakiye} />
      </div>
    </div>
  )
}
