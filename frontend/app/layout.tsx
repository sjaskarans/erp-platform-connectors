import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = { title: "Connections · Context", description: "Configure your ERP data connections" };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
