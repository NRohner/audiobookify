// Ids from the router arrive as strings ("2"), but the Rust commands take i64, and invoke
// does no coercion (fetch never cared: the id just went into a URL).
export function numericIds(args) {
  if (!args) return args;
  return Object.fromEntries(
    Object.entries(args).map(([key, value]) => [key, key.endsWith("Id") && typeof value === "string" ? Number(value) : value]),
  );
}
