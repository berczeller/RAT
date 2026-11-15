// RAT's mark: "Rat" by Thiago (Animal Pixel Art Vectors, SVG Repo, CC Attribution), https://www.svgrepo.com/svg/417294/rat
// Adapted for Decky: the original grey tones become shades of currentColor so the sidebar can tint it, and the view box
// is cropped to the rat. The unmodified original is assets/rat.svg.
import type { CSSProperties } from "react";

export function RatIcon({ size = "1em", style }: { size?: number | string; style?: CSSProperties }) {
  return (
    <svg viewBox="46 110 420 420" width={size} height={size} style={style} aria-hidden="true" shapeRendering="crispEdges">
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M435,333v-26h-25v-25.4V256h-26v-25.6V205h-26v25h-25v26h-26v-26h-25v-25h-26v-26h-25.6h-25.6h-25.6h-25.6H128v26h-26v25H77v26H51v26h26v25.2V333h25v25h26h25.6h25.6h25.6h25.6H256v-25h26v25h25.2h25.6h25.6H384h25.6h25.6H461v-25H435z M384,307h-26v-25h26V307z"
      />
      <rect x="358.011" y="281.6" width="25.989" height="25.6" fill="currentColor" opacity="0.18" />
      <g fill="currentColor" opacity="0.72">
        <polygon points="179.2,358 153.6,358 128,358 102,358 102,333 77,333 77,358.4 77,384 102.4,384 128,384 153.6,384 179.2,384 204.8,384 230,384 230,358 204.8,358" />
        <polygon points="307.2,358 282,358 282,333 256,333 256,358.4 256,384 281.6,384 307.2,384 333,384 333,358" />
      </g>
      <polygon
        fill="currentColor"
        opacity="0.5"
        points="77,410 77,384 77,358.4 77,332.8 77,307.2 77,282 51,282 51,307.2 51,332.8 51,358.4 51,384 51,410 77,410 77,435 102,435 102,410"
      />
    </svg>
  );
}
