/**
 * Smart auto-scroll hook — manages scroll-to-bottom behaviour during streaming.
 *
 * Scrolls to bottom when new content arrives and user is near the bottom.
 * Pauses when user scrolls up. Exposes a "scroll to bottom" button trigger.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface UseAutoScrollOptions {
  /** Distance from bottom (px) to consider "at bottom". Default: 100 */
  threshold?: number;
  /** Dependencies that trigger a scroll check. */
  deps?: unknown[];
}

export function useAutoScroll({
  threshold = 100,
  deps = [],
}: UseAutoScrollOptions = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [showScrollButton, setShowScrollButton] = useState(false);

  const checkIsAtBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= threshold;
  }, [threshold]);

  const handleScroll = useCallback(() => {
    const atBottom = checkIsAtBottom();
    setIsAtBottom(atBottom);
    setShowScrollButton(!atBottom);
  }, [checkIsAtBottom]);

  // Auto-scroll when deps change (new tokens)
  useEffect(() => {
    if (isAtBottom && containerRef.current) {
      containerRef.current.scrollTo({
        top: containerRef.current.scrollHeight,
        behavior: "instant",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({
        top: containerRef.current.scrollHeight,
        behavior: "smooth",
      });
      setIsAtBottom(true);
      setShowScrollButton(false);
    }
  }, []);

  return {
    containerRef,
    handleScroll,
    scrollToBottom,
    isAtBottom,
    showScrollButton,
  };
}
