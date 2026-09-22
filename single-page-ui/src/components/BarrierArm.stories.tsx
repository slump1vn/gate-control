import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import BarrierArm from './BarrierArm';

const meta: Meta<typeof BarrierArm> = {
  title: 'Gate/BarrierArm',
  component: BarrierArm,
  tags: ['autodocs'],
};

export default meta;
type Story = StoryObj<typeof BarrierArm>;

export const Closed: Story = { args: { state: 'down' } };
export const Open: Story = { args: { state: 'up' } };
export const Moving: Story = { args: { state: 'moving', position: 0.4 } };
export const NoFeedback: Story = { args: { state: null } };
