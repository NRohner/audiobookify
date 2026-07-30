import { Loader2 } from "lucide-react";

const variants = {
  primary: "bg-primary text-on-primary hover:opacity-90",
  secondary: "bg-muted text-foreground hover:bg-border",
  ghost: "text-foreground hover:bg-muted",
  destructive: "bg-destructive text-white hover:opacity-90",
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
      className={`inline-flex items-center justify-center gap-2 h-10 px-4 rounded-lg text-sm font-medium
        transition-colors duration-150 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50
        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring
        ${variants[variant]} ${className}`}
      {...props}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}
