"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";

export interface ImageArtifact {
  name: string;
  url: string;
  kind?: string;
}

export interface ImageLightboxProps {
  images: ImageArtifact[];
  initialIndex: number;
  open: boolean;
  onOpenChange(open: boolean): void;
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 5;
const SCALE_STEP = 0.25;

interface Point {
  x: number;
  y: number;
}

interface DragGesture {
  pointerId: number;
  start: Point;
  startPan: Point;
}

interface PinchGesture {
  startDistance: number;
  startScale: number;
  startPan: Point;
  startCenter: Point;
}

export function clampScale(value: number) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

function clampIndex(index: number, length: number) {
  if (length <= 0) return 0;
  return Math.min(length - 1, Math.max(0, Math.trunc(index) || 0));
}

function distance(first: Point, second: Point) {
  return Math.hypot(second.x - first.x, second.y - first.y);
}

function center(first: Point, second: Point): Point {
  return { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
}

function relativeToStage(stage: HTMLElement, point: Point): Point {
  const rect = stage.getBoundingClientRect();
  return {
    x: point.x - (rect.left + rect.width / 2),
    y: point.y - (rect.top + rect.height / 2),
  };
}

function focusableElements(dialog: HTMLElement) {
  return Array.from(
    dialog.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hasAttribute("aria-hidden"));
}

export function ImageLightbox({
  images,
  initialIndex,
  open,
  onOpenChange,
}: ImageLightboxProps) {
  const [index, setIndex] = useState(() => clampIndex(initialIndex, images.length));
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [failedUrls, setFailedUrls] = useState<Set<string>>(() => new Set());

  const dialogRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  const wasOpenRef = useRef(false);
  const scaleRef = useRef(1);
  const panRef = useRef<Point>({ x: 0, y: 0 });
  const pointersRef = useRef<Map<number, Point>>(new Map());
  const dragRef = useRef<DragGesture | null>(null);
  const pinchRef = useRef<PinchGesture | null>(null);

  onOpenChangeRef.current = onOpenChange;

  const commitTransform = useCallback((nextScale: number, nextPan: Point) => {
    const boundedScale = clampScale(nextScale);
    scaleRef.current = boundedScale;
    panRef.current = nextPan;
    setScale(boundedScale);
    setPan(nextPan);
  }, []);

  const clearPointers = useCallback(() => {
    pointersRef.current.clear();
    dragRef.current = null;
    pinchRef.current = null;
  }, []);

  const resetTransform = useCallback(() => {
    clearPointers();
    commitTransform(1, { x: 0, y: 0 });
  }, [clearPointers, commitTransform]);

  const zoomBy = useCallback(
    (amount: number) => {
      commitTransform(scaleRef.current + amount, panRef.current);
    },
    [commitTransform],
  );

  const navigate = useCallback(
    (direction: number) => {
      if (images.length <= 1) return;
      resetTransform();
      setIndex((current) => {
        const bounded = clampIndex(current, images.length);
        return (bounded + direction + images.length) % images.length;
      });
    },
    [images.length, resetTransform],
  );

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      clearPointers();
      return;
    }
    if (images.length === 0) {
      wasOpenRef.current = false;
      onOpenChangeRef.current(false);
      return;
    }
    if (!wasOpenRef.current) {
      setIndex(clampIndex(initialIndex, images.length));
      resetTransform();
    } else {
      setIndex((current) => clampIndex(current, images.length));
    }
    wasOpenRef.current = true;
  }, [clearPointers, images.length, initialIndex, open, resetTransform]);

  const selectedIndex = clampIndex(index, images.length);
  const selected = images[selectedIndex];

  useEffect(() => {
    if (selected?.url) resetTransform();
  }, [resetTransform, selected?.url]);

  useEffect(() => {
    if (!open || images.length === 0) return;
    const dialog = dialogRef.current;
    if (!dialog) return;

    openerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Tab") {
        const focusable = focusableElements(dialog);
        if (focusable.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const active = document.activeElement;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (active === first || active === dialog || !dialog.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || active === dialog || !dialog.contains(active))) {
          event.preventDefault();
          first.focus();
        }
        return;
      }

      switch (event.key) {
        case "Escape":
          event.preventDefault();
          onOpenChangeRef.current(false);
          break;
        case "+":
        case "=":
          event.preventDefault();
          zoomBy(SCALE_STEP);
          break;
        case "-":
          event.preventDefault();
          zoomBy(-SCALE_STEP);
          break;
        case "0":
          event.preventDefault();
          resetTransform();
          break;
        case "ArrowLeft":
          event.preventDefault();
          navigate(-1);
          break;
        case "ArrowRight":
          event.preventDefault();
          navigate(1);
          break;
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = originalOverflow;
      clearPointers();
      const opener = openerRef.current;
      if (opener?.isConnected) opener.focus();
      openerRef.current = null;
    };
  }, [clearPointers, images.length, navigate, open, resetTransform, zoomBy]);

  useEffect(() => {
    if (!open || images.length === 0) return;
    const stage = stageRef.current;
    if (!stage) return;

    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const currentScale = scaleRef.current;
      const nextScale = clampScale(
        currentScale + (event.deltaY < 0 ? SCALE_STEP : -SCALE_STEP),
      );
      if (nextScale === currentScale) return;

      const rect = stage.getBoundingClientRect();
      const cursor = {
        x: event.clientX - (rect.left + rect.width / 2),
        y: event.clientY - (rect.top + rect.height / 2),
      };
      const ratio = nextScale / currentScale;
      const currentPan = panRef.current;
      commitTransform(nextScale, {
        x: cursor.x - (cursor.x - currentPan.x) * ratio,
        y: cursor.y - (cursor.y - currentPan.y) * ratio,
      });
    };

    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [commitTransform, images.length, open]);

  const pointerPoint = (event: ReactPointerEvent): Point => ({
    x: event.clientX,
    y: event.clientY,
  });

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const points = pointersRef.current;
    points.set(event.pointerId, pointerPoint(event));
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture may be unavailable in older touch WebViews.
    }

    if (points.size === 1 && scaleRef.current > 1) {
      dragRef.current = {
        pointerId: event.pointerId,
        start: pointerPoint(event),
        startPan: panRef.current,
      };
      pinchRef.current = null;
      return;
    }

    if (points.size === 2) {
      const [first, second] = Array.from(points.values());
      pinchRef.current = {
        startDistance: Math.max(1, distance(first, second)),
        startScale: scaleRef.current,
        startPan: panRef.current,
        startCenter: relativeToStage(
          event.currentTarget,
          center(first, second),
        ),
      };
      dragRef.current = null;
    }
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const points = pointersRef.current;
    if (!points.has(event.pointerId)) return;
    points.set(event.pointerId, pointerPoint(event));

    if (points.size >= 2 && pinchRef.current) {
      const [first, second] = Array.from(points.values());
      const pinch = pinchRef.current;
      const currentCenter = relativeToStage(
        event.currentTarget,
        center(first, second),
      );
      const nextScale = clampScale(
        pinch.startScale * (distance(first, second) / pinch.startDistance),
      );
      const scaleRatio = nextScale / pinch.startScale;
      commitTransform(nextScale, {
        x:
          currentCenter.x -
          pinch.startCenter.x * scaleRatio +
          pinch.startPan.x * scaleRatio,
        y:
          currentCenter.y -
          pinch.startCenter.y * scaleRatio +
          pinch.startPan.y * scaleRatio,
      });
      return;
    }

    const drag = dragRef.current;
    if (
      points.size === 1 &&
      drag?.pointerId === event.pointerId &&
      scaleRef.current > 1
    ) {
      const point = pointerPoint(event);
      commitTransform(scaleRef.current, {
        x: drag.startPan.x + point.x - drag.start.x,
        y: drag.startPan.y + point.y - drag.start.y,
      });
    }
  };

  const endPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    const points = pointersRef.current;
    if (!points.has(event.pointerId)) return;
    points.delete(event.pointerId);
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // The browser may already have released capture on cancellation.
    }
    dragRef.current = null;
    pinchRef.current = null;
  };

  if (!open || images.length === 0 || !selected) return null;

  const failed = failedUrls.has(selected.url);
  const dialog = (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="图片查看器"
      tabIndex={-1}
      className="image-lightbox-overlay fixed inset-0 z-[1000] flex flex-col bg-black/90 text-white"
    >
      <div
        data-testid="image-lightbox-controls"
        className="image-lightbox-controls flex flex-wrap items-center justify-center gap-2 p-3"
      >
        <button
          type="button"
          aria-label="上一张图片"
          disabled={images.length <= 1}
          onClick={() => navigate(-1)}
        >
          上一张
        </button>
        <span aria-live="polite" className="min-w-0 max-w-64 truncate font-mono text-xs">
          {selected.name}
        </span>
        <span className="text-xs text-white/70">
          {selectedIndex + 1}/{images.length}
        </span>
        <button
          type="button"
          aria-label="下一张图片"
          disabled={images.length <= 1}
          onClick={() => navigate(1)}
        >
          下一张
        </button>
        <button type="button" aria-label="缩小" onClick={() => zoomBy(-SCALE_STEP)}>
          −
        </button>
        <span aria-live="polite" className="w-12 text-center text-xs">
          {Math.round(scale * 100)}%
        </span>
        <button type="button" aria-label="放大" onClick={() => zoomBy(SCALE_STEP)}>
          +
        </button>
        <button type="button" aria-label="重置图片" onClick={resetTransform}>
          重置
        </button>
        <a href={selected.url} download={selected.name} aria-label={`下载 ${selected.name}`}>
          下载
        </a>
        <button
          type="button"
          aria-label="关闭图片查看器"
          onClick={() => onOpenChangeRef.current(false)}
        >
          关闭
        </button>
      </div>

      <div
        ref={stageRef}
        data-testid="image-lightbox-stage"
        className="image-lightbox-stage relative flex min-h-0 flex-1 items-center justify-center overflow-hidden touch-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
      >
        {failed ? (
          <div role="status" className="rounded-lg bg-black/70 p-4 text-sm">
            {selected.name} 加载失败
          </div>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={selected.url}
            src={selected.url}
            alt={selected.name}
            draggable={false}
            onError={() =>
              setFailedUrls((current) => {
                const next = new Set(current);
                next.add(selected.url);
                return next;
              })
            }
            className="image-lightbox-image max-h-full max-w-full select-none object-contain"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
              transformOrigin: "center center",
            }}
          />
        )}
      </div>
    </div>
  );

  if (typeof document === "undefined") return null;
  return createPortal(dialog, document.body);
}
