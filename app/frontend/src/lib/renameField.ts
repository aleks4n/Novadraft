// The behaviour a rename-in-place field needs, shared by the two places a session
// can be renamed: its row in the nav tree and the title in the session bar.
//
// The click that opens the field is still in flight when the field mounts: the
// browser moves focus again on the mouse-up, and on a menu item that has just
// unmounted focus lands on whatever is now under the cursor — so the field would
// lose focus a few milliseconds after taking it and close before a key could be
// pressed. Focus is therefore taken again once that has settled, and only then does
// blur count as "clicked away".
import { useEffect, useRef, useState } from "react";

export function useRenameField() {
  const ref = useRef<HTMLInputElement>(null);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const take = () => { ref.current?.focus(); ref.current?.select(); };
    take();
    const t = setTimeout(() => { take(); setArmed(true); }, 120);
    return () => clearTimeout(t);
  }, []);

  return { ref, blurCancels: armed };
}
