import type { Version } from "./api";
export type Author = {
  user_id: string;
  identity_kind: "local" | "authenticated";
} | null;
export type SaveEvent = {
  version_id: string;
  author: Author;
  created_at: number;
  cause: string;
  kind: string;
};
export type Anchor = {
  section: number;
  paragraph: number;
  control?: number;
  table?: number;
  cell_path: {
    control: number;
    table?: number;
    cell: number;
    paragraph: number;
    row: number;
    col: number;
  }[];
};
export type CompareBlock = {
  kind: string;
  text: string;
  label: string;
  anchor: Anchor;
  last_change: SaveEvent | null;
  uncertain: boolean;
};
export type CompareChange = {
  id: string;
  kind: string;
  left: CompareBlock | null;
  right: CompareBlock | null;
  history: SaveEvent[];
  uncertain: boolean;
};
export type CompareResult = {
  left: Version;
  right: Version;
  changes: CompareChange[];
  warnings: string[];
  history_complete: boolean;
  history_versions: number;
  reverse: boolean;
};
export type Rect = {
  pageIndex: number;
  x: number;
  y: number;
  width: number;
  height: number;
};
export type PageLocation = { page: number | null; rects: Rect[]; note: string };
export type ComparisonRenderer = {
  ready: Promise<void>;
  open(bytes: Uint8Array, sha256: string): Promise<{ pages: number }>;
  locate(block: CompareBlock): PageLocation;
  render(
    page: number,
    location?: PageLocation,
    kind?: string,
    zoom?: number,
  ): void;
  dispose(): void;
};
