import { DISCLAIMER } from '../data/routines.js';

export default function Footer() {
  return (
    <footer className="footer">
      <p>{DISCLAIMER}</p>
      <p>Your data never leaves this device.</p>
    </footer>
  );
}
