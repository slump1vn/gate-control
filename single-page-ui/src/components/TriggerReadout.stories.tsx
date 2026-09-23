import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import TriggerReadout from './TriggerReadout';
import { mockTriggerIdle, mockTriggerOccupied } from '@/lib/gate-mock-data';

const meta: Meta<typeof TriggerReadout> = {
  title: 'Gate/TriggerReadout',
  component: TriggerReadout,
  tags: ['autodocs'],
  decorators: [(Story) => <div className="max-w-sm"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof TriggerReadout>;

/** Nothing in the lane: both scores sit below their thresholds. */
export const Idle: Story = { args: { readout: mockTriggerIdle } };

/** A vehicle is in the read zone: presence is well past its threshold. */
export const VehiclePresent: Story = { args: { readout: mockTriggerOccupied } };

/** A vehicle is there but barely registers: the read zone is too large or misplaced. */
export const PresenceTooLow: Story = {
  args: { readout: { ...mockTriggerIdle, state: 'motion', presence: 0.031 } },
};

export const NotReportedYet: Story = { args: { readout: null } };
