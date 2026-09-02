import Foundation
import Security

enum KeychainACLRevoker {
    static let foreignServices = [
        "Grok Bot Safe Storage",
        "Cursor Safe Storage",
        "Claude Safe Storage",
    ]

    @discardableResult
    static func revokeTokeiTrust() -> [String] {
        var changed: [String] = []
        for service in foreignServices where revoke(service: service) {
            changed.append(service)
        }
        GrokBotQuotaBridge.clearAuthorizationMarker()
        return changed
    }

    static func revokeOnceOnLaunch() {
        let key = "revokedForeignKeychainACL.v1"
        guard !UserDefaults.standard.bool(forKey: key) else { return }
        _ = revokeTokeiTrust()
        UserDefaults.standard.set(true, forKey: key)
    }

    private static func revoke(service: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecMatchLimit as String: kSecMatchLimitAll,
            kSecReturnRef as String: true,
        ]
        var raw: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &raw)
        guard status == errSecSuccess else { return false }
        guard let items = raw as? [SecKeychainItem] else { return false }
        var didChange = false
        for item in items {
            if stripTokei(from: item) { didChange = true }
        }
        return didChange
    }

    private static func stripTokei(from item: SecKeychainItem) -> Bool {
        var access: SecAccess?
        guard SecKeychainItemCopyAccess(item, &access) == errSecSuccess, let access else {
            return false
        }
        var aclList: CFArray?
        guard SecAccessCopyACLList(access, &aclList) == errSecSuccess,
              let acls = aclList as? [SecACL] else { return false }
        guard let selfApp = trustedApp(at: Bundle.main.bundleURL) ??
                trustedApp(at: URL(fileURLWithPath: "/Applications/Tokei.app")) else {
            return false
        }
        var changed = false
        for acl in acls {
            var applicationList: CFArray?
            var description: CFString?
            var prompt = SecKeychainPromptSelector()
            guard SecACLCopyContents(acl, &applicationList, &description, &prompt) == errSecSuccess else {
                continue
            }
            let apps = (applicationList as? [SecTrustedApplication]) ?? []
            let kept = apps.filter { app in !sameTrustedApp(app, selfApp) }
            if kept.count == apps.count { continue }
            let keptArray = kept as CFArray
            if SecACLSetContents(acl, keptArray, description ?? "" as CFString, prompt) == errSecSuccess {
                changed = true
            }
        }
        guard changed else { return false }
        return SecKeychainItemSetAccess(item, access) == errSecSuccess
    }

    private static func trustedApp(at url: URL) -> SecTrustedApplication? {
        var app: SecTrustedApplication?
        let status = SecTrustedApplicationCreateFromPath(url.path, &app)
        return status == errSecSuccess ? app : nil
    }

    private static func sameTrustedApp(_ lhs: SecTrustedApplication, _ rhs: SecTrustedApplication) -> Bool {
        var left: CFData?
        var right: CFData?
        guard SecTrustedApplicationCopyData(lhs, &left) == errSecSuccess,
              SecTrustedApplicationCopyData(rhs, &right) == errSecSuccess,
              let left, let right else { return false }
        return (left as Data) == (right as Data)
    }
}
