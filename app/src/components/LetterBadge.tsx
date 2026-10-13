// LetterBadge — circular letter glyph for a coin symbol. Pure render:
// no assets, no images, no network. Colors come from theme tokens only.
//
// House rules: the glyph is the symbol's first letter, uppercased; an
// empty symbol falls back to "?" rather than rendering nothing.
export interface LetterBadgeProps {
  /** Coin symbol, e.g. "BTC" — only the first letter is drawn. */
  symbol: string;
  /** Box edge length in px (default 32). */
  size?: number;
}

export function LetterBadge({ symbol, size = 32 }: LetterBadgeProps) {
  const letter = symbol.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center rounded-full border border-border bg-bg-elevated font-semibold text-accent"
      style={{
        width: size,
        height: size,
        fontSize: Math.max(10, Math.round(size * 0.45)),
      }}
    >
      {letter}
    </span>
  );
}
