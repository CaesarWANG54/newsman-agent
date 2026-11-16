import { NewsDashboard } from "@/components/news-dashboard";
import { getDashboardData } from "@/lib/api";

export default async function Home() {
  const data = await getDashboardData();
  return <NewsDashboard data={data} />;
}
