import { useEffect, useState } from "react";

// An <audio> for a file on disk. `load` resolves to an asset URL (api.*AudioSrc); it runs
// again when `cacheKey` changes. Renders nothing until then, or if the file is missing.
export default function FileAudio({ load, cacheKey, ...props }) {
  const [src, setSrc] = useState(null);

  useEffect(() => {
    let live = true;
    setSrc(null);
    load().then(
      (url) => live && setSrc(url),
      () => {},
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  return src ? <audio controls src={src} {...props} /> : null;
}
