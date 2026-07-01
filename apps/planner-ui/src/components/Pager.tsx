import styles from "./Pager.module.css";

interface Props {
  page: number;
  limit: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}

// Server-driven pager for the Pending queue (the BFF paginates; the client
// only ever holds the current page). Not shown on the Decided tab, which
// still fetches a high-limit merge of the 3 decided statuses (see usePlanner).
export function Pager({ page, limit, total, onPrev, onNext }: Props) {
  if (total === 0) return null;
  const start = page * limit + 1;
  const end = Math.min(total, (page + 1) * limit);
  return (
    <div className={styles.bar}>
      <span>
        Showing {start}–{end} of {total}
      </span>
      <button type="button" onClick={onPrev} disabled={page === 0}>
        Prev
      </button>
      <button type="button" onClick={onNext} disabled={end >= total}>
        Next
      </button>
    </div>
  );
}
