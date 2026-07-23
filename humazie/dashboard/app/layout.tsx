import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Humazie Bot",
  description: "Autonomous product review dashboard for Strata",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <div>
            <p className="brand">Humazie Bot</p>
            <p className="tag">Strata product review</p>
          </div>
          <nav className="nav">
            <a href="/">Runs</a>
            <a href="/review/new">New review</a>
          </nav>
        </header>
        <main className="main">{children}</main>
      </body>
    </html>
  );
}
