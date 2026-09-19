import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as SonnerToaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "Cubicador IA Local",
  description: "Extracción local y trazable del cuadro de cubicación de planos PDF.",
  keywords: ["PDF", "extracción", "IA", "tablas", "catalogación", "datos", "análisis documental"],
  authors: [{ name: "Extractor PDF IA" }],
  icons: {
    icon: "/logo.svg",
  },
  openGraph: {
    title: "Extractor PDF + IA",
    description: "Catalogación automática de tablas técnicas en PDFs",
    siteName: "Extractor PDF IA",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Extractor PDF + IA",
    description: "Catalogación automática de tablas técnicas en PDFs",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body className="antialiased bg-background text-foreground">
        {children}
        <Toaster />
        <SonnerToaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
