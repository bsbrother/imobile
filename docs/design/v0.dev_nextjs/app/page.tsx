import { MarketStats } from "@/components/market-stats"
import { StockTable } from "@/components/stock-table"

export default function Home() {
  return (
    <div className="min-h-screen bg-background">
      <main className="container mx-auto px-4 py-6 max-w-7xl">
        <MarketStats />
        <StockTable />
      </main>
    </div>
  )
}
