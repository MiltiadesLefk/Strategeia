import { formatR } from '../components/common';

/** "Worst price during the trade" as a signed R figure: the backend stores the
 *  adverse excursion as a non-negative magnitude, shown here as the loss it is. */
export function formatAdverseR(maeR: number | null | undefined): string {
  return maeR === null || maeR === undefined ? '—' : formatR(maeR === 0 ? 0 : -maeR);
}
