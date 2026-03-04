/**
 * ScrollableBar — horizontal scrollable container with arrow navigation.
 *
 * Replaces native scrollbars with left/right arrow buttons that appear
 * contextually. Clicking an arrow scrolls by 200px with smooth animation.
 *
 * Dependents:
 *   Used by GraphToolbar and GraphEdgeToolbar for label pill lists.
 */
import { useRef, useState, useEffect, useCallback, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface ScrollableBarProps {
  children: ReactNode;
  className?: string;
}

export function ScrollableBar({ children, className }: ScrollableBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  /** Recalculate arrow visibility based on scroll position. */
  const updateArrows = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 2);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
  }, []);

  /* Listen to scroll and resize events. */
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateArrows();
    el.addEventListener("scroll", updateArrows, { passive: true });
    const ro = new ResizeObserver(updateArrows);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", updateArrows);
      ro.disconnect();
    };
  }, [updateArrows]);

  /** Scroll the container by a pixel delta with smooth animation. */
  const scrollBy = (delta: number) => {
    scrollRef.current?.scrollBy({ left: delta, behavior: "smooth" });
  };

  return (
    <div className={`relative flex items-center overflow-hidden ${className ?? ""}`}>
      {/* Left arrow — visible only when scrolled past start */}
      {canScrollLeft && (
        <button
          onClick={() => scrollBy(-200)}
          className="absolute left-0 z-10 flex h-full w-6 items-center justify-center
                     bg-gradient-to-r from-neutral-bg1 via-neutral-bg1/80 to-transparent
                     text-text-muted hover:text-text-primary transition-colors"
          aria-label="Scroll left"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      )}

      {/* Scrollable content — native scrollbar hidden via CSS */}
      <div
        ref={scrollRef}
        className="flex items-center gap-1 flex-1 overflow-x-auto"
        style={{ scrollbarWidth: "none" }}
      >
        {children}
      </div>

      {/* Right arrow — visible only when more content to the right */}
      {canScrollRight && (
        <button
          onClick={() => scrollBy(200)}
          className="absolute right-0 z-10 flex h-full w-6 items-center justify-center
                     bg-gradient-to-l from-neutral-bg1 via-neutral-bg1/80 to-transparent
                     text-text-muted hover:text-text-primary transition-colors"
          aria-label="Scroll right"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
