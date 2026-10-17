// 設計系統：Mantine 主題與 app.css 的 token 同一套（品牌藍、圓角、字級、間距）；元件預設尺寸統一 sm，表單欄位一律有標籤。
import { ActionIcon, Badge, Button, NativeSelect, NumberInput, Paper, Table, TextInput, Textarea, createTheme, type MantineColorsTuple } from '@mantine/core';

const brand: MantineColorsTuple = ['#E9F0FD', '#D3E1FB', '#B9CFF5', '#8FB1EF', '#6394EA', '#3F7DE8', '#2C73EA', '#1F5BC4', '#19499D', '#133A7C'];
const dark: MantineColorsTuple = ['#E7EEF1', '#D3DCE1', '#B7C3CA', '#8A9AA6', '#4A5B67', '#2A3842', '#1B2731', '#16222B', '#121C24', '#0F171E'];

export const theme = createTheme({
  primaryColor: 'brand', primaryShade: { light: 6, dark: 5 },
  colors: { brand, dark },
  white: '#FFFFFF', black: '#1C2B33',
  fontFamily: "'Plus Jakarta Sans', 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif",
  fontFamilyMonospace: "'IBM Plex Mono', ui-monospace, monospace",
  fontSizes: { xs: '11.5px', sm: '13px', md: '14px', lg: '16px', xl: '20px' },
  headings: { fontWeight: '700', sizes: { h1: { fontSize: '20px' }, h2: { fontSize: '16px' }, h3: { fontSize: '14px' }, h4: { fontSize: '13px' } } },
  defaultRadius: 'md', radius: { sm: '6px', md: '8px', lg: '12px' },
  spacing: { xs: '6px', sm: '10px', md: '14px', lg: '20px', xl: '28px' },
  components: {
    Button: Button.extend({ defaultProps: { size: 'sm', variant: 'default' } }),
    ActionIcon: ActionIcon.extend({ defaultProps: { variant: 'subtle', size: 'sm' } }),
    Badge: Badge.extend({ defaultProps: { variant: 'light', radius: 'sm', size: 'sm', tt: 'none', fw: 600 } }),
    Paper: Paper.extend({ defaultProps: { withBorder: true, radius: 'lg', p: 'md', shadow: 'xs' } }),
    TextInput: TextInput.extend({ defaultProps: { size: 'sm' } }),
    Textarea: Textarea.extend({ defaultProps: { size: 'sm' } }),
    NumberInput: NumberInput.extend({ defaultProps: { size: 'sm' } }),
    NativeSelect: NativeSelect.extend({ defaultProps: { size: 'sm' } }),
    Table: Table.extend({ defaultProps: { fz: 'sm', verticalSpacing: 'xs', horizontalSpacing: 'sm', highlightOnHover: true } }),
  },
});
