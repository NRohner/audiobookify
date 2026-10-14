import { spineLook } from "../lib/shelf.js";

export function clothStyle(book) {
  const { color, tone } = spineLook(book);
  return {
    background: `var(--rr-spine-${color})`,
    color: `var(--rr-spine-text-${tone})`,
  };
}

// Typographic cover for books without art. `mini` is the 48×70 thumbnail.
export default function BookCover({ book, mini = false, className = "" }) {
  if (mini) {
    return (
      <div
        aria-hidden="true"
        className={`rr-cover justify-center shrink-0 w-12 h-[70px] text-[22px] shadow-none! ${className}`}
        style={clothStyle(book)}
      >
        {book.title.charAt(0).toUpperCase()}
      </div>
    );
  }
  const long = book.title.length > 22;
  return (
    <div
      role="img"
      aria-label={`Cover of ${book.title}`}
      className={`rr-cover w-64 h-[372px] px-7 pt-10 pb-9 ${className}`}
      style={clothStyle(book)}
    >
      <div className="rr-rule" />
      <p className={`mt-12 font-medium leading-[1.05] ${long ? "text-[34px]" : "text-[44px]"} line-clamp-5`}>
        {book.title}
      </p>
      <div className="mt-6 flex items-center gap-1.5 opacity-70" aria-hidden="true">
        <span className="w-3 border-t border-current" />
        <span className="size-2.5 rotate-45 border border-current" />
        <span className="w-3 border-t border-current" />
      </div>
      {book.author && (
        <p className="mt-auto text-[15px] uppercase tracking-[0.14em] line-clamp-2">{book.author}</p>
      )}
    </div>
  );
}
