/**
 * @module usePausableSimulation
 *
 * Pause/resume hook for the force-graph physics simulation.
 *
 * Provides two pause modes:
 *   1. **Auto-pause on hover** — freezes the simulation so nodes stop
 *      jittering while the user inspects tooltips. Resumes after 300ms.
 *   2. **Manual pause** — locks the simulation until explicitly toggled.
 *
 * Operates on any ref implementing `{ setFrozen(boolean): void }`,
 * which is satisfied by {@link GraphCanvasHandle}.
 *
 * @param canvasRef — React ref to the graph canvas imperative handle
 * @returns `{ isPaused, handleMouseEnter, handleMouseLeave, handleTogglePause, resetPause }`
 */
import { useState, useCallback, useRef, useEffect, type RefObject } from 'react';

/** Return shape for callers. */
interface PausableSimulationResult {
  isPaused: boolean;
  handleMouseEnter: () => void;
  handleMouseLeave: () => void;
  handleTogglePause: () => void;
  resetPause: () => void;
}

/** Minimal interface — anything with a setFrozen method. */
interface Freezable {
  setFrozen: (frozen: boolean) => void;
}

export function usePausableSimulation(
  canvasRef: RefObject<Freezable | null>,
): PausableSimulationResult {
  const [isPaused, setIsPaused] = useState(false);
  const [manualPause, setManualPause] = useState(false);
  const resumeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Freeze on mouse enter — prevents node jitter during tooltip inspection. */
  const handleMouseEnter = useCallback(() => {
    if (resumeTimeoutRef.current) {
      clearTimeout(resumeTimeoutRef.current);
      resumeTimeoutRef.current = null;
    }
    canvasRef.current?.setFrozen(true);
    setIsPaused(true);
  }, [canvasRef]);

  /** Resume 300ms after mouse leave — unless manually paused. */
  const handleMouseLeave = useCallback(() => {
    if (manualPause) return;
    resumeTimeoutRef.current = setTimeout(() => {
      canvasRef.current?.setFrozen(false);
      setIsPaused(false);
      resumeTimeoutRef.current = null;
    }, 300);
  }, [manualPause, canvasRef]);

  /** Toggle manual pause — overrides hover-based auto-pause. */
  const handleTogglePause = useCallback(() => {
    if (manualPause) {
      setManualPause(false);
      canvasRef.current?.setFrozen(false);
      setIsPaused(false);
    } else {
      setManualPause(true);
      canvasRef.current?.setFrozen(true);
      setIsPaused(true);
    }
  }, [manualPause, canvasRef]);

  /** Hard reset — clears manual pause and unfreezes. */
  const resetPause = useCallback(() => {
    setManualPause(false);
    canvasRef.current?.setFrozen(false);
    setIsPaused(false);
  }, [canvasRef]);

  /* Auto-pause 5 seconds after mount so the layout settles then freezes. */
  useEffect(() => {
    const autoPauseTimer = setTimeout(() => {
      if (!manualPause && !isPaused) {
        setManualPause(true);
        canvasRef.current?.setFrozen(true);
        setIsPaused(true);
      }
    }, 5000);
    return () => clearTimeout(autoPauseTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Cleanup resume timeout on unmount. */
  useEffect(() => {
    return () => {
      if (resumeTimeoutRef.current) clearTimeout(resumeTimeoutRef.current);
    };
  }, []);

  return { isPaused, handleMouseEnter, handleMouseLeave, handleTogglePause, resetPause };
}
