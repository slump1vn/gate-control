import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import Navbar from './Navbar';
import { MockAuthProvider } from './AuthContext';

const meta: Meta<typeof Navbar> = {
  title: 'Components/Navbar',
  component: Navbar,
  tags: ['autodocs'],
  parameters: { nextjs: { appDirectory: true } },
};

export default meta;
type Story = StoryObj<typeof Navbar>;

export const Default: Story = {};

export const Operator: Story = {
  decorators: [(Story) => <MockAuthProvider roles={['gate_operator']} username="guard1"><Story /></MockAuthProvider>],
};

export const Admin: Story = {
  decorators: [(Story) => <MockAuthProvider roles={['gate_admin', 'gate_operator']} username="admin"><Story /></MockAuthProvider>],
};
