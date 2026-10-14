import { Loader2 } from "lucide-react";

const variants = {
  primary: "h-10 px-5 bg-accent text-on-accent text-[14px] font-bold hover:brightness-95",
  secondary: "h-10 px-5 bg-card text-ink border border-line text-[14px] font-semibold hover:bg-oat",
  dark: "h-[34px] px-4 bg-ink text-card text-[14px] font-bold hover:opacity-90",
  quiet: "h-8 px-2 text-ink-muted text-meta font-semibold hover:text-ink",
};

export default function Button({
  variant = "primary",
  loading = false,
  disabled = false,
  className = "",
  children,
  ...props
}) {
  return (
    <button
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-full whitespace-nowrap
        transition-[background-color,filter,opacity,color] duration-150
        disabled:cursor-not-allowed disabled:opacity-45 ${variants[variant]} ${className}`}
      {...props}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}

// 30px round icon button (play, cancel, edit). Always pass an aria-label.
export function IconButton({ className = "", children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex size-[30px] shrink-0 items-center justify-center rounded-full border border-line bg-card
        text-ink transition-colors duration-150 hover:bg-oat disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function PageHeader({ title, children }) {
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-8">
      <h1 className="text-title mr-auto">{title}</h1>
      {children}
    </header>
  );
}

export function ErrorNote({ children }) {
  if (!children) return null;
  return (
    <p role="alert" className="mb-5 rounded-tile bg-accent-soft px-4 py-3 text-meta text-accent-text">
      {children}
    </p>
  );
}
