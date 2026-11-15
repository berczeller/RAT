// [ LOOK ]: HDR (RenoDX) and ReShade as two folds, one open at a time. Their advanced rows show with Show advanced
// (end of the TUNE tab).
import { PanelSection } from "@decky/ui";
import { useState } from "react";
import type { Ctx } from "../ctx";
import { Fold } from "../theme";
import { HdrTab } from "./hdr";
import { ReShadeTab } from "./reshade";

const lookFold: Record<string, string | null> = {};

export function LookTab({ ctx }: { ctx: Ctx }) {
  const [open, setOpen] = useState<string | null>(ctx.key in lookFold ? lookFold[ctx.key] : "hdr");
  const toggle = (id: string) => {
    const next = open === id ? null : id;
    lookFold[ctx.key] = next;
    setOpen(next);
  };
  return (
    <PanelSection>
      <Fold id="look-hdr" title="HDR · RenoDX" icon="hdr" open={open === "hdr"} onToggle={() => toggle("hdr")}>
        <HdrTab key={ctx.key} ctx={ctx} bare />
      </Fold>
      <Fold id="look-reshade" title="ReShade" icon="reshade" open={open === "reshade"} onToggle={() => toggle("reshade")}>
        <ReShadeTab key={ctx.key} ctx={ctx} bare />
      </Fold>
    </PanelSection>
  );
}
