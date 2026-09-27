import "./globals.css";

export const metadata = {
  title: "FundVault — Fund Management System",
  description: "Create named fund databases, record detailed transactions and track live balances."
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
