// Sparkline — inline SVG trend line from closed-bar values. Pure render:
// no chart library, no assets, no network. The stroke follows
// `currentColor`, so the theme-token class on the <svg> drives the color.
//
// House rules:
//   * an empty values array renders an empty frame — no line, and never
//     fabricated data;
//   * a single value renders a lone dot (a one-point polyline draws
//     nothing, so the flat line is drawn explicitly);
//   * values are never smoothed, resampled or extrapolated.
export interface SparklineProps {
  /** Closed-bar values in chronological order. */
  values: number[];
  /** true = uptrend (long token), false = downtrend (short token). */
  up: boolean;
}

const WIDTH = 96;
const HEIGHT = 28;
const INSET = 1.5; // keeps the stroke inside the viewBox

export function Sparkline({ values, up }: SparklineProps) {
  const strokeClass = up ? "text-long" : "text-short";
  const label = up ? "trend up" : "trend down";

  if (values.length === 0) {
    return (
      <svg
        className={strokeClass}
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="no trend data"
      />
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const usableH = HEIGHT - INSET * 2;
  const usableW = WIDTH - INSET * 2;

  const toXY = (v: number, i: number): [number, number] => {
    const x =
      values.length > 1
        ? INSET + (i * usableW) / (values.length - 1)
        : WIDTH / 2;
    // span === 0 (flat values): draw the flat line mid-height instead of
    // dividing by zero.
    const y = span > 0 ? HEIGHT - INSET - ((v - min) / span) * usableH : HEIGHT / 2;
    return [Number(x.toFixed(2)), Number(y.toFixed(2))];
  };

  const points = values
    .map(toXY)
    .map(([x, y]) => `${x},${y}`)
    .join(" ");

  return (
    <svg
      className={strokeClass}
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={label}
    >
      {values.length === 1 ? (
        <circle cx={WIDTH / 2} cy={HEIGHT / 2} r={1.5} fill="currentColor" />
      ) : (
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}
