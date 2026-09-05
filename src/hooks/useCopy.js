import { useCallback, useEffect, useRef, useState } from 'react';

/** Copies `text` to the clipboard; `copied` flips true for two seconds. */
export default function useCopy(text) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt('Copy this address', text);
    }
  }, [text]);

  useEffect(() => () => clearTimeout(timer.current), []);
  return [copied, copy];
}
