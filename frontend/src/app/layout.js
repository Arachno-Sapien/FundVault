import "./globals.css";

export const metadata = {
  title: "FundVault — Fund Management System",
  description: "Migrated Next.js UI for FundVault"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
