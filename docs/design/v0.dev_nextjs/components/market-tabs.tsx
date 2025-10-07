"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Download, Upload, BarChart3, Settings2 } from "lucide-react"

export function MarketTabs() {
  const [activeTab, setActiveTab] = useState("持仓")

  const tabs = ["持仓", "交易记录", "转账记录"]

  return (
    <div className="mb-6">
      <div className="flex items-center justify-between border-b">
        <div className="flex items-center gap-6">
          {tabs.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 text-sm font-medium transition-colors relative ${
                activeTab === tab ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab}
              {activeTab === tab && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary" />}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <Download className="h-3 w-3" />
            导入
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <Upload className="h-3 w-3" />
            卖出
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <BarChart3 className="h-3 w-3" />
            数据转移
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-xs gap-1">
            <Settings2 className="h-3 w-3" />
            显示转移
          </Button>
        </div>
      </div>
    </div>
  )
}
