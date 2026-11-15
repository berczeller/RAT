// The upscaling path picker (Native DLSS / Spoofed → FSR / Spoofed → XeSS) and the colour-coded upscaler list, used on
// the Upscale tab (setup) and the Tune tab.
import { DialogButton, Focusable } from "@decky/ui";
import type { Ctx } from "./ctx";
import { api } from "./api";
import { PickerItem } from "./picker";
import { FAMILY, hint, Line, RS, Row } from "./theme";

const PATH_FAMILY: Record<string, keyof typeof FAMILY> = { native: "dlss", fsr: "fsr", xess: "xess" };
const PATH_HINT: Record<string, string> = {
  native: "The game's DLSS request runs a DLSS network: real DLSS on NVIDIA RTX, d4r's DLSS on AMD RDNA3/4, HelixSR's Model E.",
  fsr: "The game is shown an NVIDIA GPU so it offers DLSS; the injector answers with AMD FSR.",
  xess: "The game is shown an NVIDIA GPU so it offers DLSS; the injector answers with Intel XeSS.",
};

export function ModePicker({ ctx, disabled }: { ctx: Ctx; disabled?: boolean }) {
  const { d, ov, key } = ctx;
  const paths = d.paths.filter((p) => p in ov.paths);
  if (!paths.length) return null;
  const cur = d.path;
  return (
    <>
      <Row id="mode">
        <div style={{ width: "100%" }}>
          <div style={{ fontSize: 10, color: RS.muted, letterSpacing: 0.8, textTransform: "uppercase", marginBottom: 5 }}>Upscaling mode</div>
          <Focusable style={{ display: "flex", gap: 5 }} flow-children="horizontal">
            {paths.map((p) => {
              const f = FAMILY[PATH_FAMILY[p] ?? "neutral"];
              const on = cur === p;
              return (
                <DialogButton
                  key={p}
                  disabled={disabled}
                  className="RS_Tab"
                  style={{
                    flex: 1,
                    color: on ? "#141828" : f.c,
                    background: on ? `linear-gradient(135deg, ${f.c}, ${f.c}cc)` : undefined,
                    borderColor: on ? f.c : `${f.c}66`,
                    fontSize: 9.5,
                  }}
                  onClick={async () => {
                    if (on) return;
                    const r = await ctx.act(() => api.setMode(key, p));
                    if (r && d.editing_installed && d.record) ctx.syncLaunch();
                  }}
                >
                  {ov.paths[p].toUpperCase()}
                </DialogButton>
              );
            })}
          </Focusable>
          {cur && PATH_HINT[cur] && hint(PATH_HINT[cur]) && <Line size={10}>{PATH_HINT[cur]}</Line>}
        </div>
      </Row>
    </>
  );
}

export function UpscalerPicker({ ctx, disabled }: { ctx: Ctx; disabled?: boolean }) {
  const { d, addon, key } = ctx;
  const routeNow = d.route ?? null;
  // NVIDIA-only entries (real DLSS through OptiScaler) are hidden on GPUs that cannot run them
  const ups = addon.upscalers.filter((x) => (!x.route || x.route === routeNow) && (!d.upscalers || d.upscalers.includes(x.id)));
  const cur = ups.find((x) => x.id === d.upscaler) ?? ups[0];
  return (
    <Row id="upscaler">
      <PickerItem
        label="Upscaler"
        description={hint(cur?.hint)}
        disabled={disabled}
        options={ups.map((u) => ({ data: u.id, label: u.label, family: u.family ?? "neutral", sub: u.model || undefined }))}
        selected={cur?.id}
        onChange={async (o) => {
          const r = await ctx.act(() => api.setUpscaler(key, o.data));
          if (r && d.editing_installed) ctx.syncLaunch();
        }}
      />
    </Row>
  );
}
