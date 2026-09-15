import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as SonnerToaster } from "@/components/ui/sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Extractor PDF + IA | Catalogación Automática de Tablas",
  description: "Sistema local para extraer y catalogar datos de tablas en PDFs usando inteligencia artificial. Sube un PDF, define términos clave y obtén una tabla exportable.",
  keywords: ["PDF", "extracción", "IA", "tablas", "catalogación", "datos", "análisis documental"],
  authors: [{ name: "Extractor PDF IA" }],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
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
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        {children}
        <Toaster />
        <SonnerToaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
