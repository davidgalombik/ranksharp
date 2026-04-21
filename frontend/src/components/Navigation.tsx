"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useState, useRef } from "react";
import clsx from "clsx";

const topLinks = [
  { href: "/", label: "Dashboard", exact: true },
  { href: "/products", label: "Current Products", exact: true },
  { href: "/retailers", label: "Retailers" },
  { href: "/products/historical", label: "Historical Products", exact: true },
];

const trendsDropdown = [
  { href: "/trends", label: "Product Trends" },
  { href: "/fragrance-trends", label: "Fragrance Trends" },
  { href: "/instore", label: "In-store Trends" },
  { href: "/aldi", label: "Aldi Trends" },
];

const TRENDS_PREFIXES = trendsDropdown.map((l) => l.href);

export default function Navigation() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isTrendsActive = TRENDS_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(prefix + "/")
  );

  function handleMouseEnter() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    setOpen(true);
  }

  function handleMouseLeave() {
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  }

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

        <nav className="flex gap-1 items-center">
          {/* Dashboard */}
          <Link
            href="/"
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              path === "/" ? "bg-stone-900 text-white" : "text-stone-600 hover:bg-stone-100"
            )}
          >
            Dashboard
          </Link>

          {/* Current Products */}
          <Link
            href="/products"
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              path === "/products" ? "bg-stone-900 text-white" : "text-stone-600 hover:bg-stone-100"
            )}
          >
            Current Products
          </Link>

          {/* In-store Products */}
          <Link
            href="/instore-products"
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              path === "/instore-products" || path.startsWith("/instore-products/")
                ? "bg-stone-900 text-white"
                : "text-stone-600 hover:bg-stone-100"
            )}
          >
            In-store Products
          </Link>

          {/* Trends dropdown */}
          <div
            className="relative"
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          >
            <button
              className={clsx(
                "flex items-center gap-1 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                isTrendsActive ? "bg-stone-900 text-white" : "text-stone-600 hover:bg-stone-100"
              )}
            >
              Analysis
              <svg
                className={clsx("w-3.5 h-3.5 transition-transform", open && "rotate-180")}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {open && (
              <div className="absolute left-0 top-full mt-1 w-44 bg-white border border-stone-200 rounded-xl shadow-lg py-1 z-50">
                {trendsDropdown.map(({ href, label }) => (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setOpen(false)}
                    className={clsx(
                      "block px-4 py-2 text-sm font-medium transition-colors",
                      path === href || path.startsWith(href + "/")
                        ? "bg-stone-100 text-stone-900"
                        : "text-stone-600 hover:bg-stone-50 hover:text-stone-900"
                    )}
                  >
                    {label}
                  </Link>
                ))}
              </div>
            )}
          </div>

          {/* Retailers (Scrape) */}
          <Link
            href="/retailers"
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              path === "/retailers" || path.startsWith("/retailers/")
                ? "bg-stone-900 text-white"
                : "text-stone-600 hover:bg-stone-100"
            )}
          >
            Scrape
          </Link>

          {/* Historical Products */}
          <Link
            href="/products/historical"
            className={clsx(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              path === "/products/historical"
                ? "bg-stone-900 text-white"
                : "text-stone-600 hover:bg-stone-100"
            )}
          >
            Historical Products
          </Link>
        </nav>
      </div>
    </header>
  );
}
