"use client"

import { useState } from "react"
import { Home, TrendingUp, Wallet, BarChart3, Settings, ChevronRight, ChevronLeft, Sun, Moon } from "lucide-react"
import { useTheme } from "@/hooks/use-theme"
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip"

export function Sidebar() {
  const [isExpanded, setIsExpanded] = useState(false)
  const { theme, toggleTheme } = useTheme()

  const menuItems = [
    { icon: Home, label: "首页", active: true },
    { icon: TrendingUp, label: "行情", active: false },
    { icon: Wallet, label: "持仓", active: false },
    { icon: BarChart3, label: "交易", active: false },
  ]

  return (
    <TooltipProvider>
      <aside
        className={`fixed left-0 top-0 h-screen bg-sidebar border-r border-sidebar-border transition-all duration-300 z-50 ${
          isExpanded ? "w-56" : "w-16"
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Navigation Items */}
          <nav className="flex-1 py-4">
            {menuItems.map((item, index) => {
              const button = (
                <button
                  key={index}
                  className={`w-full flex items-center gap-3 px-4 py-3 transition-colors ${
                    item.active
                      ? "text-sidebar-primary bg-sidebar-accent border-r-2 border-sidebar-primary"
                      : "text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                  }`}
                >
                  <item.icon className="h-5 w-5 flex-shrink-0" />
                  {isExpanded && <span className="text-sm font-medium">{item.label}</span>}
                </button>
              )

              if (!isExpanded) {
                return (
                  <Tooltip key={index}>
                    <TooltipTrigger asChild>{button}</TooltipTrigger>
                    <TooltipContent side="right" sideOffset={10}>
                      {item.label}
                    </TooltipContent>
                  </Tooltip>
                )
              }

              return button
            })}
          </nav>

          {/* Settings Section */}
          <div className="border-t border-sidebar-border py-4">
            {/* Theme Toggle */}
            {!isExpanded ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={toggleTheme}
                    className="w-full flex items-center gap-3 px-4 py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
                  >
                    {theme === "dark" ? (
                      <Sun className="h-5 w-5 flex-shrink-0" />
                    ) : (
                      <Moon className="h-5 w-5 flex-shrink-0" />
                    )}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={10}>
                  {theme === "dark" ? "浅色模式" : "深色模式"}
                </TooltipContent>
              </Tooltip>
            ) : (
              <button
                onClick={toggleTheme}
                className="w-full flex items-center gap-3 px-4 py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
              >
                {theme === "dark" ? (
                  <Sun className="h-5 w-5 flex-shrink-0" />
                ) : (
                  <Moon className="h-5 w-5 flex-shrink-0" />
                )}
                <span className="text-sm font-medium">{theme === "dark" ? "浅色模式" : "深色模式"}</span>
              </button>
            )}

            {/* Settings */}
            {!isExpanded ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button className="w-full flex items-center gap-3 px-4 py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors">
                    <Settings className="h-5 w-5 flex-shrink-0" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={10}>
                  设置
                </TooltipContent>
              </Tooltip>
            ) : (
              <button className="w-full flex items-center gap-3 px-4 py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors">
                <Settings className="h-5 w-5 flex-shrink-0" />
                <span className="text-sm font-medium">设置</span>
              </button>
            )}

            {/* Expand/Collapse Toggle */}
            {!isExpanded ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => setIsExpanded(!isExpanded)}
                    className="w-full flex items-center justify-center py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground transition-colors"
                  >
                    <ChevronRight className="h-5 w-5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={10}>
                  展开侧边栏
                </TooltipContent>
              </Tooltip>
            ) : (
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full flex items-center justify-center py-3 text-sidebar-foreground/60 hover:text-sidebar-foreground transition-colors"
              >
                <ChevronLeft className="h-5 w-5" />
              </button>
            )}
          </div>
        </div>
      </aside>
    </TooltipProvider>
  )
}
