import type { ReactNode } from "react";

export type IconName =
  | "activity"
  | "arrow-right"
  | "check"
  | "check-circle"
  | "chevron-right"
  | "clock"
  | "credit-card"
  | "database"
  | "eye"
  | "flask"
  | "gauge"
  | "info"
  | "layers"
  | "pause"
  | "play"
  | "refresh"
  | "search"
  | "shield"
  | "sliders"
  | "spark"
  | "stop"
  | "target"
  | "triangle-alert"
  | "x";

const paths: Record<IconName, ReactNode> = {
  activity: <><path d="M3 12h4l2.2-7 4.1 14L16 9l1.8 3H21" /></>,
  "arrow-right": <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  "check-circle": <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></>,
  "chevron-right": <path d="m9 18 6-6-6-6" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  "credit-card": <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 10h18" /><path d="M7 15h3" /></>,
  database: <><ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5" /><path d="M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7" /></>,
  eye: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></>,
  flask: <><path d="M9 3h6" /><path d="M10 3v5l-5.5 9.3A1.8 1.8 0 0 0 6 20h12a1.8 1.8 0 0 0 1.5-2.7L14 8V3" /><path d="M7.3 15h9.4" /></>,
  gauge: <><path d="M4.5 17a8 8 0 1 1 15 0" /><path d="m12 12 4-4" /><path d="M7 17h.01M12 19h.01M17 17h.01" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 16 9 5 9-5" /></>,
  pause: <><rect x="7" y="5" width="3" height="14" rx="1" /><rect x="14" y="5" width="3" height="14" rx="1" /></>,
  play: <path d="m9 5 10 7-10 7V5Z" />,
  refresh: <><path d="M20 11a8 8 0 0 0-14-4L4 9" /><path d="M4 4v5h5" /><path d="M4 13a8 8 0 0 0 14 4l2-2" /><path d="M20 20v-5h-5" /></>,
  search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></>,
  shield: <><path d="M12 3 19 6v5c0 4.6-2.8 8-7 10-4.2-2-7-5.4-7-10V6l7-3Z" /><path d="m9 12 2 2 4-4" /></>,
  sliders: <><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="11" cy="18" r="2" /></>,
  spark: <><path d="m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4L12 3Z" /><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6L19 16Z" /></>,
  stop: <rect x="6" y="6" width="12" height="12" rx="1.5" />,
  target: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2" /></>,
  "triangle-alert": <><path d="m12 3 9 16H3L12 3Z" /><path d="M12 9v4" /><path d="M12 16h.01" /></>,
  x: <><path d="m6 6 12 12" /><path d="m18 6-12 12" /></>,
};

export function Icon({ name, size = 18, strokeWidth = 1.8, className }: { name: IconName; size?: number; strokeWidth?: number; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      {paths[name]}
    </svg>
  );
}
