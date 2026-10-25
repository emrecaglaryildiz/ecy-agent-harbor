/* All user-facing civil time belongs to Istanbul, independent of the device. */
const HarborTime = Object.freeze({
  zone: 'Europe/Istanbul',
  scheduleEpoch(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) throw new Error('Invalid Istanbul date');
    const epoch = Date.parse(value + '+03:00') / 1000;
    if (!Number.isFinite(epoch)) throw new Error('Invalid Istanbul date');
    return epoch;
  }
});
if (typeof module !== 'undefined') module.exports = HarborTime;
