import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface TableCaptionProps {
  children: ReactNode;
}

/** Visually-hidden `<caption>` — announced to screen readers, invisible on screen. */
export function TableCaption({ children }: TableCaptionProps) {
  return <caption className="sr-only">{children}</caption>;
}

export interface EmptyRowProps {
  children: ReactNode;
  colSpan: number;
}

/** One `<tr><td colSpan>` for a table's empty state, muted like the rest of the design system. */
export function EmptyRow({ children, colSpan }: EmptyRowProps) {
  return (
    <tr>
      <td colSpan={colSpan} className={cn("p-4 text-sm text-ink-2")}>
        {children}
      </td>
    </tr>
  );
}
