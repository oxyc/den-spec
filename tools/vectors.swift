// Writes vectors/library-v2.json: every derivation in wire/library-v2.md §1–2, for a fixed library key and
// fixed nonces, computed with CryptoKit. Run from the repository root:
//
//   swift tools/vectors.swift > vectors/library-v2.json
//
// Clients check themselves against the file; nothing here is secret.

import CryptoKit
import Foundation

func hex(_ bytes: some Sequence<UInt8>) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
func bytes(_ key: SymmetricKey) -> Data { key.withUnsafeBytes { Data($0) } }
func base64url(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}

let libraryKey = SymmetricKey(data: Data(0..<32))
func derive(salt: String, info: String, count: Int) -> SymmetricKey {
    HKDF<SHA256>.deriveKey(inputKeyMaterial: libraryKey, salt: Data(salt.utf8), info: Data(info.utf8),
                           outputByteCount: count)
}
let id = derive(salt: "den/library/salt/v1", info: "den/library/id/v1", count: 16)
let encKey = derive(salt: "den/library/v2", info: "enc", count: 32)
let macKey = derive(salt: "den/library/v2", info: "mac", count: 32)
let token = derive(salt: "den/library/v2", info: "token", count: 32)

let stamp = #"[1789000000000,0,"a1b2c3d4e5f60718"]"#
let rows: [(name: String, plaintext: String)] = [
    ("rec:movie:550", #"{"kind":"rec","schema":2,"title":{"type":"movie","id":550},"#
        + #""status":{"value":"watchlist","at":\#(stamp)},"#
        + #""resume":{"value":0,"at":\#(stamp),"viewing":0},"#
        + #""reaction":{"value":null,"at":\#(stamp)},"deleted":{"value":false,"at":\#(stamp)},"#
        + #""dismissed":{"value":false,"at":\#(stamp)},"episodesReset":null,"#
        + #""addedAt":1789000000000,"watchedAt":null}"#),
    ("ep:tv:1399:1:2", #"{"kind":"ep","schema":2,"title":{"type":"tv","id":1399},"season":1,"episode":2,"#
        + #""progress":{"value":1,"at":\#(stamp),"viewing":0,"seconds":3000}}"#),
]

var rowVectors: [[String: String]] = []
for (index, row) in rows.enumerated() {
    let mac = Data(HMAC<SHA256>.authenticationCode(for: Data(row.name.utf8), using: macKey))
    let nonce = Data((0..<12).map { UInt8($0 + 16 * index) })
    let box = try AES.GCM.seal(Data(row.plaintext.utf8), using: encKey, nonce: try AES.GCM.Nonce(data: nonce),
                               authenticating: mac)
    rowVectors.append([
        "name": row.name, "k": hex(mac), "nonce": hex(nonce), "plaintext": row.plaintext,
        "v": base64url(nonce + box.ciphertext + box.tag),
    ])
}

let vectors: [String: Any] = [
    "libraryKey": hex(bytes(libraryKey)),
    "derived": ["id": hex(bytes(id)), "encKey": hex(bytes(encKey)), "macKey": hex(bytes(macKey)),
                "token": hex(bytes(token))],
    "rows": rowVectors,
]
let json = try JSONSerialization.data(withJSONObject: vectors,
                                      options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
print(String(decoding: json, as: UTF8.self))
