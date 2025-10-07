"use client"

import { TrendingUp } from "lucide-react"

export function MarketStats() {
  return (
    <div className="mb-6">
      <div className="flex items-baseline gap-3 mb-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">总市值(元)</span>
          <TrendingUp className="h-4 w-4 text-muted-foreground" />
        </div>
      </div>

      <div className="flex items-baseline gap-4 mb-6">
        <h2 className="text-5xl font-bold text-[#e74c3c] tabular-nums">850545.20</h2>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <div>
          <div className="text-xs text-muted-foreground mb-1">今日变化</div>
          <div className="text-sm font-medium">0.00(0.00%)</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">浮动盘变化</div>
          <div className="text-sm font-medium text-[#e74c3c]">+40388.20(+50.42%)</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">累计变化</div>
          <div className="text-sm font-medium text-[#e74c3c]">+407483.40(+92.70%)</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">总资产</div>
          <div className="text-sm font-medium">877483.40</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">现金</div>
          <div className="text-sm font-medium">26938.20</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-1">本金</div>
          <div className="text-sm font-medium">470000.00</div>
        </div>
      </div>
    </div>
  )
}
