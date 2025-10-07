"use client"

import { Button } from "@/components/ui/button"
import { X } from "lucide-react"

interface Stock {
  name: string
  code: string
  price: number
  change: number
  changePercent: number
  volume: number
  amount: number
  marketValue: number
  floatChange: number
  floatChangePercent: number
  cumulativeChange: number
  cumulativeChangePercent: number
}

const stocks: Stock[] = [
  {
    name: "雪山水泥",
    code: "600425",
    price: 22.83,
    change: 0.08,
    changePercent: 0.35,
    volume: 45660.0,
    amount: 2000,
    marketValue: 12.72,
    floatChange: 20220.0,
    floatChangePercent: 78.48,
    cumulativeChange: 20220.0,
    cumulativeChangePercent: 78.48,
  },
  {
    name: "宝方鑫",
    code: "52000870",
    price: 8.93,
    change: 0.23,
    changePercent: 2.53,
    volume: 89200.0,
    amount: 10000,
    marketValue: 2.12,
    floatChange: 68300.0,
    floatChangePercent: 325.24,
    cumulativeChange: 68300.0,
    cumulativeChangePercent: 325.24,
  },
  {
    name: "中国平安",
    code: "51600579",
    price: 5.16,
    change: -0.04,
    changePercent: -0.77,
    volume: 46440.0,
    amount: 9000,
    marketValue: 4.17,
    floatChange: 7740.0,
    floatChangePercent: 20.0,
    cumulativeChange: 8865.0,
    cumulativeChangePercent: 23.91,
  },
  {
    name: "中国玉米",
    code: "52000099",
    price: 12.46,
    change: 0.44,
    changePercent: 3.66,
    volume: 121111.2,
    amount: 9720,
    marketValue: 6.99,
    floatChange: 53071.2,
    floatChangePercent: 78.0,
    cumulativeChange: 54140.3,
    cumulativeChangePercent: 78.57,
  },
  {
    name: "浅蓝3A",
    code: "52000000",
    price: 10.29,
    change: 0.94,
    changePercent: 10.05,
    volume: 514500.0,
    amount: 50000,
    marketValue: 5.47,
    floatChange: 241000.0,
    floatChangePercent: 88.12,
    cumulativeChange: 241000.0,
    cumulativeChangePercent: 88.12,
  },
  {
    name: "欧洲宏观",
    code: "51600063",
    price: 6.21,
    change: 0.08,
    changePercent: 1.47,
    volume: 33534.0,
    amount: 5400,
    marketValue: 3.44,
    floatChange: 13554.0,
    floatChangePercent: 67.84,
    cumulativeChange: 14958.0,
    cumulativeChangePercent: 74.86,
  },
]

export function StockTable() {
  return (
    <div className="bg-card rounded-lg border">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="text-left py-3 px-4 text-xs font-medium text-muted-foreground">名称/代码</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">现价</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">涨跌</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">市值</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">持仓</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">成本/成本</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">浮动盘变化</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">累计变化</th>
              <th className="text-right py-3 px-4 text-xs font-medium text-muted-foreground">操作</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map((stock, index) => (
              <tr key={stock.code} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                <td className="py-3 px-4">
                  <div className="font-medium text-sm">{stock.name}</div>
                  <div className="text-xs text-muted-foreground">{stock.code}</div>
                </td>
                <td className="text-right py-3 px-4">
                  <div className="text-sm font-medium tabular-nums">{stock.price.toFixed(2)}</div>
                </td>
                <td className="text-right py-3 px-4">
                  <div
                    className={`text-sm font-medium tabular-nums ${
                      stock.change >= 0 ? "text-[#e74c3c]" : "text-[#27ae60]"
                    }`}
                  >
                    {stock.change >= 0 ? "+" : ""}
                    {stock.change.toFixed(2)}({stock.changePercent.toFixed(2)}%)
                  </div>
                </td>
                <td className="text-right py-3 px-4">
                  <div className="text-sm tabular-nums">{stock.volume.toFixed(2)}</div>
                </td>
                <td className="text-right py-3 px-4">
                  <div className="text-sm tabular-nums">{stock.amount}</div>
                </td>
                <td className="text-right py-3 px-4">
                  <div className="text-sm tabular-nums">{stock.marketValue.toFixed(2)}</div>
                </td>
                <td className="text-right py-3 px-4">
                  <div
                    className={`text-sm font-medium tabular-nums ${
                      stock.floatChange >= 0 ? "text-[#e74c3c]" : "text-[#27ae60]"
                    }`}
                  >
                    {stock.floatChange.toFixed(2)}({stock.floatChangePercent.toFixed(2)}%)
                  </div>
                </td>
                <td className="text-right py-3 px-4">
                  <div
                    className={`text-sm font-medium tabular-nums ${
                      stock.cumulativeChange >= 0 ? "text-[#e74c3c]" : "text-[#27ae60]"
                    }`}
                  >
                    {stock.cumulativeChange.toFixed(2)}({stock.cumulativeChangePercent.toFixed(2)}%)
                  </div>
                </td>
                <td className="text-right py-3 px-4">
                  <div className="flex items-center justify-end gap-2">
                    <Button variant="link" size="sm" className="h-auto p-0 text-xs text-primary">
                      记录
                    </Button>
                    <Button variant="link" size="sm" className="h-auto p-0 text-xs text-primary">
                      卖出
                    </Button>
                    <Button variant="ghost" size="icon" className="h-6 w-6">
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
