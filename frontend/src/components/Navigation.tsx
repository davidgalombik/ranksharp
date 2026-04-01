"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/reports", label: "Reports" },
  { href: "/trends", label: "Trends" },
  { href: "/products", label: "Products" },
  { href: "/retailers", label: "Retailers" },
  { href: "/aldi", label: "Aldi Trends" },
  { href: "/progress", label: "Scrape Progress" },
];

export default function Navigation() {
  const path = usePathname();
  return (
    <header className="bg-white border-b border-stone-200 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
        <Link href="/" className="flex items-center flex-shrink-0">
          <Image
            src="/ranksharp-logo.webp"
            alt="Rank Sharp Industries"
            width={160}
            height={48}
            className="h-10 w-auto object-contain"
            priority
          />
        </Link>
        <nav className="flex gap-1">
          {links.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={clsx(
                "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                (href === "/" ? path === "/" : path.startsWith(href))
                  ? "bg-stone-900 text-white"
                  : "text-stone-600 hover:bg-stone-100"
              )}
            >
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
