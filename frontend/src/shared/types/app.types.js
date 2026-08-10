/**
 * @typedef {'light' | 'dark'} ThemeMode
 * @typedef {'motorA' | 'motorB'} MotorId
 * @typedef {'chat' | 'documents' | 'library' | 'settings'} SectionId
 *
 * @typedef SourceItem
 * @property {string} title
 * @property {string} source
 * @property {string | null} link_original
 * @property {string | null} deep_link
 * @property {number | null} page
 * @property {string | null} article_number
 * @property {string} law_status
 * @property {string | null} excerpt
 *
 * @typedef ChatRecord
 * @property {string} id
 * @property {string} question
 * @property {string} answer
 * @property {SourceItem[]} sources
 * @property {string} provider_used
 * @property {string} createdAt
 *
 * @typedef AppState
 * @property {ThemeMode} theme
 * @property {MotorId} motor
 * @property {SectionId} activeSection
 * @property {ChatRecord[]} history
 * @property {string | null} selectedChatId
 */

export {}
