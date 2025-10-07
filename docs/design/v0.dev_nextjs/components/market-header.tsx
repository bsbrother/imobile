import { Button } from "@/components/ui/button"
import { Bell, User, Settings, RefreshCw } from "lucide-react"

export function MarketHeader() {
  return (
    <header className="border-b bg-card">
      <div className="container mx-auto px-4 max-w-7xl">
        <div className="flex items-center justify-between h-14">
          <div className="flex items-center gap-8">
            <h1 className="text-xl font-semibold">金融市场</h1>
            <nav className="hidden md:flex items-center gap-6">
              <a href="#" className="text-sm hover:text-primary transition-colors">
                市场
              </a>
              <a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">
                日盘交易
              </a>
              <a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">
                浮动盘交易
              </a>
              <a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">
                市值
              </a>
              <a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">
                合资产
              </a>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="h-9 w-9">
              <RefreshCw className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-9 w-9">
              <Bell className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-9 w-9">
              <Settings className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-9 w-9">
              <User className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </header>
  )
}
